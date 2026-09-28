"""Stremio Subtitle query endpoints."""

import asyncio
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

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


def extract_meta_from_filename(filename: str) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
    """Extract (title, year, season, episode) from release filename."""
    if not filename:
        return "", None, None, None

    clean = urllib.parse.unquote(filename)
    clean = re.sub(r"\.(mkv|mp4|avi|ts|mov|m4v|iso|wmv|flv)$", "", clean, flags=re.IGNORECASE)

    # Season and Episode (e.g. S01E14)
    s_match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", clean)
    season = int(s_match.group(1)) if s_match else None
    episode = int(s_match.group(2)) if s_match else None

    # Year (19xx or 20xx)
    y_match = re.search(r"\b(19\d\d|20\d\d)\b", clean)
    year = int(y_match.group(1)) if y_match else None

    # Cut off title before season, year, or resolution markers
    cutoff_patterns = [
        r"[Ss]\d{1,2}[Ee]\d{1,2}",
        r"\b(19\d\d|20\d\d)\b",
        r"\b(1080p|720p|2160p|4k|bluray|web-dl|webrip|hdtv|remux)\b"
    ]
    min_idx = len(clean)
    for pat in cutoff_patterns:
        m = re.search(pat, clean, flags=re.IGNORECASE)
        if m and m.start() < min_idx and m.start() > 0:
            min_idx = m.start()

    title_part = clean[:min_idx].strip(" .-_")
    title = re.sub(r"[._]", " ", title_part).strip()

    return title, year, season, episode


def parse_media_id(media_type: str, id_str: str, extra: str = "") -> VideoQueryMeta:
    """Parse Stremio media ID string and optional extra params into structured VideoQueryMeta."""
    clean_id = urllib.parse.unquote(re.sub(r"\.json$", "", id_str))

    season: Optional[int] = None
    episode: Optional[int] = None

    if clean_id.startswith(("tmdb:", "kitsu:")):
        # Harbor or TMDB catalog format: tmdb:12345 or tmdb:12345:1:14 or tmdb:movie:12345
        tokens = clean_id.split(":")
        prefix = tokens[0]
        if len(tokens) > 2 and tokens[1] in ("movie", "tv"):
            imdb_id = f"{prefix}:{tokens[1]}:{tokens[2]}"
            rest = tokens[3:]
        elif len(tokens) > 1:
            imdb_id = f"{prefix}:{tokens[1]}"
            rest = tokens[2:]
        else:
            imdb_id = clean_id
            rest = []

        if len(rest) >= 2:
            try:
                season = int(rest[0])
                episode = int(rest[1])
            except (ValueError, TypeError):
                pass
        elif len(rest) == 1:
            try:
                season = int(rest[0])
            except (ValueError, TypeError):
                pass
    else:
        parts = clean_id.split(":")
        imdb_id = parts[0]
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


def extract_release_tags(cand_title: str) -> List[str]:
    """
    Extract and standardize release quality attributes (Source, Resolution, Codec, Audio, Group)
    from subtitle candidate title for display and player release matching.
    """
    tags: List[str] = []
    text = (cand_title or "").strip()
    text = re.sub(r"\.(zip|rar|7z|tar|gz|bz2|srt|ass|ssa|vtt)$", "", text, flags=re.IGNORECASE)

    # 1. Chinese markers to English tags
    if re.search(r"官方|官译", text, re.IGNORECASE):
        tags.append("Official")
    if re.search(r"精修|转载精修", text, re.IGNORECASE):
        tags.append("Refined")
    if re.search(r"全\s*\d+\s*[集话話]|全集|合集|全部|整季|季全", text, re.IGNORECASE):
        tags.append("Complete")

    # 2. Source / Quality
    if re.search(r"\b(remux)\b", text, re.IGNORECASE):
        tags.append("REMUX")
    elif re.search(r"\b(blu-?ray|bdrip|brrip|blu\.ray)\b", text, re.IGNORECASE):
        tags.append("BluRay")
    elif re.search(r"\b(web-?dl|amzn[\.\s_-]?web-?dl)\b", text, re.IGNORECASE):
        tags.append("WEB-DL")
    elif re.search(r"\b(web-?rip)\b", text, re.IGNORECASE):
        tags.append("WEBRip")
    elif re.search(r"\b(web)\b", text, re.IGNORECASE):
        tags.append("WEB-DL")
    elif re.search(r"\b(hdtv|hdtvrip)\b", text, re.IGNORECASE):
        tags.append("HDTV")
    elif re.search(r"\b(dvdrip|dvd)\b", text, re.IGNORECASE):
        tags.append("DVDRip")
    elif re.search(r"\b(cam-?rip|cam|telesync|ts)\b", text, re.IGNORECASE):
        tags.append("CAM-Rip")

    # 3. Resolution
    res_match = re.search(r"(2160p|4k|1080p|1080i|720p|480p|576p)", text, re.IGNORECASE)
    if res_match:
        val = res_match.group(1).lower()
        tags.append("4K" if val == "4k" else val)

    # 4. Video Codec
    if re.search(r"\b(x265|hevc|h[\.\s_-]?265)\b", text, re.IGNORECASE):
        tags.append("x265")
    elif re.search(r"\b(x264|avc|h[\.\s_-]?264)\b", text, re.IGNORECASE):
        tags.append("x264")

    # 5. HDR / Bitdepth / Dolby Vision
    if re.search(r"\b(10-?bit)\b", text, re.IGNORECASE):
        tags.append("10bit")
    if re.search(r"\b(hdr10\+|hdr10|hdr)\b", text, re.IGNORECASE):
        tags.append("HDR")
    if re.search(r"\b(dv|dolby[\.\s_-]?vision)\b", text, re.IGNORECASE):
        tags.append("DV")

    # 6. Audio
    if re.search(r"\b(atmos)\b", text, re.IGNORECASE):
        tags.append("Atmos")
    elif re.search(r"\b(ddp[\.\s_-]?5[\.\s_-]?1|ddp)\b", text, re.IGNORECASE):
        tags.append("DDP5.1")

    # 7. Release Groups
    group_match = re.search(r'[-. ](PSA|KINGDOM|POKE|SCOPE|YTS|YIFY|RARBG|YYeTs|FLUX|NTb|BONE)\b', text, re.IGNORECASE)
    if group_match:
        tags.append(group_match.group(1).upper())
    elif re.search(r"人人影视|yyets", text, re.IGNORECASE):
        tags.append("YYeTs")

    # Fallback: if no recognized tags found, extract clean ASCII tokens
    if not tags:
        clean = re.sub(r'[^A-Za-z0-9]', ' ', text)
        for w in clean.split():
            if len(w) > 2 and not w.isdigit() and w.lower() not in ("the", "and", "srt", "sub"):
                tags.append(w)
                if len(tags) >= 3:
                    break

    # Deduplicate while preserving order
    seen = set()
    deduped: List[str] = []
    for t in tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            deduped.append(t)
    return deduped


def format_subtitle_info(cand: SubtitleCandidate, query: VideoQueryMeta) -> Tuple[str, str]:
    """
    Format subtitle label (for Stremio/Harbor UI matching OpenSubtitles v3 style)
    and filename (for URL/MPV player track identification).
    Returns:
        (label, filename)
        Example label:    "The End of Oak Street · WEB-DL · 1080p · x264 · [Bilingual SubHD]"
        Example filename: "The.End.of.Oak.Street.2026.WEB-DL.1080p.x264.[Bilingual.SubHD].srt"
    """
    prov_tag = "SubHD" if cand.provider.lower() == "subhd" else "Zimuku"

    if cand.tags.bilingual:
        lang_tag = "Bilingual"
    elif "cht" in cand.tags.lang:
        lang_tag = "Cht"
    else:
        lang_tag = "Chs"

    tag_bracket_space = f"[{lang_tag} {prov_tag}]"
    tag_bracket_dot = f"[{lang_tag}.{prov_tag}]"

    # Clean title
    clean_title = re.sub(r'[^A-Za-z0-9 ]', ' ', query.title or "Subtitle").strip()
    clean_title = " ".join(clean_title.split()) or "Subtitle"
    dot_title = clean_title.replace(" ", ".")

    # Season/Episode or Year tag
    if query.is_tv and query.episode is not None:
        ep_tag = f"S{query.season or 1:02d}E{query.episode:02d}"
    elif query.year:
        ep_tag = str(query.year)
    else:
        ep_tag = ""

    tags = extract_release_tags(cand.title or "")

    # Build human-readable label (OpenSubtitles v3 style with ' · ' separator)
    label_parts = [clean_title]
    if ep_tag and ep_tag not in label_parts:
        label_parts.append(ep_tag)
    label_parts.extend(tags)
    label_parts.append(tag_bracket_space)
    label = " · ".join(label_parts)

    # Build pure URL-safe ASCII filename (dot-separated)
    fn_parts = [dot_title]
    if ep_tag and ep_tag not in fn_parts:
        fn_parts.append(ep_tag)
    fn_parts.extend(tags)
    fn_parts.append(tag_bracket_dot)
    filename_body = ".".join(fn_parts)
    filename_body = re.sub(r"\.+", ".", filename_body).strip("._- ")
    if len(filename_body) > 120:
        truncated = filename_body[:120]
        filename_body = truncated.rsplit(".", 1)[0] if "." in truncated else truncated

    filename = f"{filename_body}.srt"
    return label, filename


def format_subtitle_filename(cand: SubtitleCandidate, query: VideoQueryMeta) -> str:
    """Convenience helper returning the formatted filename string."""
    _, fn = format_subtitle_info(cand, query)
    return fn


def _is_cache_valid(items: Optional[List[Dict[str, Any]]], query_meta: VideoQueryMeta) -> bool:
    """Check if cached subtitle items have the new clean ASCII filename and label structure."""
    if not items:
        # If cache is empty, do not treat as valid if filename or title is present
        if query_meta.filename or query_meta.title:
            return False
        return True
    for it in items:
        # Require rich label
        if not it.get("label"):
            return False
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

        # Resolve media metadata (title, year, and resolved IMDb ID) via Cinemeta or TMDB
        title, year, resolved_imdb = await CinemetaClient.resolve_media_info(query_meta.media_type, query_meta.imdb_id)
        if resolved_imdb and resolved_imdb.startswith("tt") and not query_meta.imdb_id.startswith("tt"):
            logger.info(f"[Subtitles] Resolved {query_meta.imdb_id} -> {resolved_imdb}")
            query_meta.imdb_id = resolved_imdb

        if not title and query_meta.filename:
            fn_title, fn_year, fn_s, fn_ep = extract_meta_from_filename(query_meta.filename)
            if fn_title:
                title = fn_title
            if not year and fn_year:
                year = fn_year
            if query_meta.season is None and fn_s is not None:
                query_meta.season = fn_s
            if query_meta.episode is None and fn_ep is not None:
                query_meta.episode = fn_ep

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

        subhd_count = sum(1 for c in candidates if c.provider == "subhd")
        zimuku_count = sum(1 for c in candidates if c.provider == "zimuku")
        logger.info(
            f"[Subtitles] Aggregated {len(candidates)} candidates ({subhd_count} SubHD, {zimuku_count} Zimuku)"
        )

        # Rank and filter candidates
        ranked = SubtitleScorer.rank_candidates(
            candidates=candidates,
            query=query_meta,
            lang_preference=lang_pref,
            max_results=max_results
        )
        logger.info(f"[Subtitles] Returning top {len(ranked)} subtitles for {query_meta.imdb_id}")

        # Format Stremio subtitle payload with informative variant filenames and labels
        subtitles = []
        for cand in ranked:
            ep_num = query_meta.episode if query_meta.episode is not None else 0
            sub_label, sub_filename = format_subtitle_info(cand, query_meta)
            rel_path = f"/subtitles/dl/{cand.provider}/{cand.id}/{ep_num}/{sub_filename}"
            full_url = f"{base_url}{rel_path}"

            sub_item = {
                "id": f"{cand.provider}_{cand.id}_{ep_num}",
                "url": full_url,
                "lang": "chi",
                "label": sub_label,
                "subtitleFileName": sub_filename,
                "movieReleaseName": sub_filename.rsplit(".", 1)[0],
                "SubFormat": "srt",
                "path": rel_path
            }
            subtitles.append(sub_item)

        # Persist to search cache only when candidates are found
        if subtitles:
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
