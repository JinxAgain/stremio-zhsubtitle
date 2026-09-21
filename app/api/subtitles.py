"""Stremio Subtitle query endpoints."""

import asyncio
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from .manifest import parse_config
from ..cache.manager import cache_manager
from ..core.cinemeta import CinemetaClient
from ..core.scorer import SubtitleScorer
from ..providers.base import SubtitleCandidate, VideoQueryMeta
from ..providers.subhd import SubhdProvider
from ..providers.zimuku import ZimukuProvider

logger = logging.getLogger(__name__)
router = APIRouter()

subhd_provider = SubhdProvider()
zimuku_provider = ZimukuProvider()


def get_public_base_url(request: Request) -> str:
    """Derive external public URL from reverse proxy headers or request base URL."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", str(request.base_url.netloc)))
    return f"{proto}://{host}".rstrip("/")


def parse_media_id(media_type: str, id_str: str, extra: str = "") -> VideoQueryMeta:
    """Parse Stremio media ID string and optional extra params into structured VideoQueryMeta."""
    # Decode URL encoding (e.g. tt1439629%3A1%3A14 -> tt1439629:1:14)
    clean_id = urllib.parse.unquote(re.sub(r"\.json$", "", id_str))
    parts = clean_id.split(":")

    imdb_id = parts[0]
    season: Optional[int] = None
    episode: Optional[int] = None

    if len(parts) >= 3:
        try:
            season = int(parts[1])
            episode = int(parts[2])
        except (ValueError, TypeError):
            pass
    elif len(parts) == 2:
        try:
            season = int(parts[1])
        except (ValueError, TypeError):
            pass

    # Extract filename from extra path parameter if present
    filename = ""
    if extra:
        unquoted_extra = urllib.parse.unquote(re.sub(r"\.json$", "", extra))
        m = re.search(r"filename=([^&]+)", unquoted_extra)
        if m:
            filename = m.group(1).strip()

    return VideoQueryMeta(
        imdb_id=imdb_id,
        media_type=media_type,
        season=season,
        episode=episode,
        filename=filename
    )


def format_subtitle_filename(cand: SubtitleCandidate, query: VideoQueryMeta) -> str:
    """
    Generate a clean, 100% ASCII descriptive filename for Stremio subtitle variants tooltip.
    Stremio player displays the URL filename verbatim in the tooltip without decodeURIComponent,
    so non-ASCII characters become unreadable %XX%XX percent-encoded sequences.
    """
    prov_tag = "SubHD" if cand.provider.lower() == "subhd" else "Zimuku"

    # Language classification (Pure ASCII for clean tooltip rendering in Stremio)
    if cand.tags.bilingual:
        lang_tag = "Bilingual"
    elif "cht" in cand.tags.lang:
        lang_tag = "Cht"
    else:
        lang_tag = "Chs"

    tag = f"[{lang_tag}.{prov_tag}]"

    raw_title = (cand.title or "").strip()
    # Strip archive and subtitle extensions
    raw_title = re.sub(r"\.(zip|rar|7z|tar|gz|bz2|srt|ass|ssa|vtt)$", "", raw_title, flags=re.IGNORECASE).strip()

    # Translate common Chinese markers to ASCII equivalents
    replacements = [
        (r"中英双[字语]|双语|双字", " Bilingual "),
        (r"简体|简中|chs|gb", " Chs "),
        (r"繁体|繁中|cht|big5", " Cht "),
        (r"第\s*0*(\d+)\s*季", r" S\1 "),
        (r"第\s*0*(\d+)\s*[集话話]", r" E\1 "),
        (r"全\s*\d+\s*[集话話]|全集|合集|全部|整季|季全", " Complete "),
        (r"官方|官译", " Official "),
        (r"精修|转载精修", " Refined "),
        (r"原创", " Original "),
        (r"人人影视|yyets", " YYeTs "),
        (r"字幕组|压制组", " "),
    ]
    replaced = raw_title
    for pat, rep in replacements:
        replaced = re.sub(pat, rep, replaced, flags=re.IGNORECASE)

    # Clean punctuation and strip all non-ASCII characters
    replaced = re.sub(r'[\[\](){}<>/*?:"|#%&+=_\\-]', ' ', replaced)
    ascii_clean = re.sub(r'[^\x20-\x7E]', ' ', replaced)
    tokens = [t.strip('.-') for t in ascii_clean.split() if re.match(r'^[A-Za-z0-9.-]+$', t)]
    tokens = [t for t in tokens if len(t) > 1 or t.isdigit()]

    # Deduplicate tokens while preserving order
    seen = set()
    deduped_tokens = []
    for t in tokens:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            deduped_tokens.append(t)

    # Cinemeta base info
    cinemeta_title = re.sub(r'[^A-Za-z0-9.]', '.', query.title or "").strip('.')
    ep_str = f"S{query.season or 1:02d}E{query.episode:02d}" if query.is_tv and query.episode is not None else (str(query.year) if query.year else "")

    if len(deduped_tokens) >= 3 and any(t.lower() in ("s01", "s02", "1080p", "720p", "bluray", "web", "complete") for t in deduped_tokens):
        body = ".".join(deduped_tokens)
    else:
        parts = [cinemeta_title] if cinemeta_title else []
        if ep_str and ep_str.lower() not in [p.lower() for p in parts]:
            parts.append(ep_str)
        for t in deduped_tokens:
            if t.lower() not in [p.lower() for p in parts] and (
                t.upper() in ("1080P", "720P", "2160P", "4K", "BLURAY", "WEB-DL", "WEBRIP", "HDTV", "REFINED", "OFFICIAL", "COMPLETE", "YYETS")
                or t.startswith("S0") or t.startswith("E0")
            ):
                parts.append(t)
        body = ".".join(parts) if parts else (cinemeta_title or "Subtitle")

    # Clean multiple dots
    body = re.sub(r"\.+", ".", body).strip("._- ")
    if len(body) > 60:
        body = body[:60].rstrip("._- ")

    return f"{tag}.{body}.srt"


def _is_cache_valid(items: Optional[List[Dict[str, Any]]], query_meta: VideoQueryMeta) -> bool:
    """Check if cached subtitle items have the new clean ASCII filename structure."""
    if not items:
        return True
    for it in items:
        p = it.get("path", "")
        parts = p.strip("/").split("/")
        # Invalidate old format, digit.srt, or any path containing %-encoding or non-ASCII
        if (
            len(parts) < 5
            or parts[-1] in ("0.srt", f"{query_meta.episode or 0}.srt")
            or "%" in parts[-1]
            or any(ord(c) > 127 for c in parts[-1])
        ):
            return False
    return True


async def handle_subtitles_request(
    request: Request,
    media_type: str,
    id_str: str,
    extra: str = "",
    config_str: str = ""
) -> Dict[str, Any]:
    """Core handler for subtitle requests across default and configured routes."""
    if media_type not in ("movie", "series"):
        raise HTTPException(status_code=404, detail=f"Invalid media type: {media_type}")

    config = parse_config(config_str)
    lang_pref = config.get("lang", "bilingual")
    source_pref = config.get("source", "all")
    max_results = int(config.get("max_results", 8))

    query_meta = parse_media_id(media_type, id_str, extra)
    base_url = get_public_base_url(request)

    # Construct unique search cache key
    cache_key = (
        f"{query_meta.media_type}:{query_meta.imdb_id}:"
        f"{query_meta.season or 0}:{query_meta.episode or 0}:"
        f"{lang_pref}:{source_pref}:{max_results}"
    )

    # Check search cache first
    cached_results = cache_manager.get_search_results(cache_key)
    if cached_results is not None and _is_cache_valid(cached_results, query_meta):
        # Re-map base_url dynamically in case host changed
        mapped_subtitles = []
        for item in cached_results:
            item_copy = dict(item)
            item_copy["url"] = f"{base_url}{item_copy.get('path', '')}"
            mapped_subtitles.append(item_copy)
        return {"subtitles": mapped_subtitles}

    # Single-flight lock per query key to avoid duplicate scraping bursts
    flight_lock = await cache_manager.get_flight_lock(cache_key)
    async with flight_lock:
        # Re-check cache inside lock
        cached_results = cache_manager.get_search_results(cache_key)
        if cached_results is not None and _is_cache_valid(cached_results, query_meta):
            mapped_subtitles = []
            for item in cached_results:
                item_copy = dict(item)
                item_copy["url"] = f"{base_url}{item_copy.get('path', '')}"
                mapped_subtitles.append(item_copy)
            return {"subtitles": mapped_subtitles}

        # Resolve media metadata (title, year) via Cinemeta
        title, year = await CinemetaClient.resolve_metadata(query_meta.media_type, query_meta.imdb_id)
        query_meta.title = title
        query_meta.year = year
        logger.info(
            f"[Subtitles] Query: {query_meta.imdb_id} ('{query_meta.title}' {query_meta.year}) "
            f"S{query_meta.season or 0}E{query_meta.episode or 0}"
        )

        # Dispatch scraping tasks in parallel
        tasks = []
        if source_pref in ("all", "subhd"):
            tasks.append(asyncio.to_thread(subhd_provider.search, query_meta))
        if source_pref in ("all", "zimuku"):
            tasks.append(asyncio.to_thread(zimuku_provider.search, query_meta))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: List[SubtitleCandidate] = []
        for r in results:
            if isinstance(r, list):
                candidates.extend(r)
            elif isinstance(r, Exception):
                logger.warning(f"Provider search failed with exception: {r}")

        # Rank and filter candidates
        ranked = SubtitleScorer.rank_candidates(
            candidates=candidates,
            query=query_meta,
            lang_preference=lang_pref,
            max_results=max_results
        )

        # Format Stremio subtitle payload with informative variant filenames
        subtitles = []
        for cand in ranked:
            ep_num = query_meta.episode if query_meta.episode is not None else 0
            sub_filename = format_subtitle_filename(cand, query_meta)
            rel_path = f"/subtitles/dl/{cand.provider}/{cand.id}/{ep_num}/{sub_filename}"
            full_url = f"{base_url}{rel_path}"

            sub_item = {
                "id": f"{cand.provider}_{cand.id}_{ep_num}",
                "url": full_url,
                "lang": "chi",
                "path": rel_path
            }
            subtitles.append(sub_item)

        # Persist to search cache
        cache_manager.set_search_results(cache_key, subtitles)

        # Return response
        return {"subtitles": subtitles}


@router.get("/subtitles/{media_type}/{id_str}.json")
async def get_subtitles_default_no_extra(request: Request, media_type: str, id_str: str):
    """Retrieve subtitles using default settings without extra parameters."""
    return await handle_subtitles_request(request, media_type, id_str, extra="", config_str="")


@router.get("/subtitles/{media_type}/{id_str}/{extra:path}")
async def get_subtitles_default_with_extra(request: Request, media_type: str, id_str: str, extra: str):
    """Retrieve subtitles using default settings with extra parameters (videoSize, filename, etc.)."""
    return await handle_subtitles_request(request, media_type, id_str, extra=extra, config_str="")


@router.get("/{config}/subtitles/{media_type}/{id_str}.json")
async def get_subtitles_configured_no_extra(request: Request, config: str, media_type: str, id_str: str):
    """Retrieve subtitles using user-configured settings without extra parameters."""
    return await handle_subtitles_request(request, media_type, id_str, extra="", config_str=config)


@router.get("/{config}/subtitles/{media_type}/{id_str}/{extra:path}")
async def get_subtitles_configured_with_extra(request: Request, config: str, media_type: str, id_str: str, extra: str):
    """Retrieve subtitles using user-configured settings with extra parameters."""
    return await handle_subtitles_request(request, media_type, id_str, extra=extra, config_str=config)
