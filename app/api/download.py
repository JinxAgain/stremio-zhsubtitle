"""On-demand subtitle download, extraction, normalization, and streaming endpoint."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from ..cache.manager import cache_manager
from ..config import settings
from ..core.cleaner import SubtitleCleaner
from ..core.extractor import SubtitleExtractor, is_direct_subtitle
from ..providers.base import SubtitleCandidate
from ..providers.subhd import SubhdProvider
from ..providers.zimuku import ZimukuProvider

logger = logging.getLogger(__name__)
router = APIRouter()

subhd_provider = SubhdProvider()
zimuku_provider = ZimukuProvider()



@router.options("/subtitles/dl/{provider}/{sub_id}/{episode_num}/{filename:path}")
@router.options("/subtitles/dl/{provider}/{sub_id}/{episode_num}.srt")
async def download_options():
    """Handle CORS pre-flight requests for subtitle downloads."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*",
        }
    )


@router.get("/subtitles/dl/{provider}/{sub_id}/{episode_num}/{filename:path}")
@router.get("/subtitles/dl/{provider}/{sub_id}/{episode_num}.srt")
async def download_subtitle(provider: str, sub_id: str, episode_num: int, filename: str = ""):
    """
    On-demand endpoint to extract, normalize, cache, and stream clean UTF-8 SRT subtitles.
    """
    cache_filename = f"{provider}_{sub_id}_{episode_num}.srt"
    logger.info(
        f"[Download] Incoming request: provider={provider}, id={sub_id}, ep={episode_num}, filename='{filename}'"
    )

    # 1. Fast path: Return cached SRT from disk if present (<5ms)
    if cache_manager.has_subtitle(cache_filename):
        content = cache_manager.get_subtitle(cache_filename)
        if content:
            cues_count = content.count(b"-->")
            logger.info(
                f"[Download] Cache HIT: '{cache_filename}' ({len(content)} bytes, {cues_count} cues). Serving cached SRT."
            )
            return Response(
                content=content,
                media_type="text/plain; charset=utf-8",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
                    "Access-Control-Allow-Headers": "*",
                    "Cache-Control": "public, max-age=86400",
                }
            )

    logger.info(f"[Download] Cache MISS: '{cache_filename}'. Fetching from upstream {provider}...")

    # 2. Slow path: Fetch archive from upstream provider
    archive_bytes: Optional[bytes] = None
    raw_filename: str = ""

    if provider == "subhd":
        candidate = SubtitleCandidate(
            id=sub_id,
            provider="subhd",
            title="",
            page_url=f"{settings.SUBHD_BASE_URL}/a/{sub_id}"
        )
        archive_bytes, raw_filename = await asyncio.to_thread(subhd_provider.download, candidate)
    elif provider == "zimuku":
        candidate = SubtitleCandidate(
            id=sub_id,
            provider="zimuku",
            title="",
            page_url=f"{settings.ZIMUKU_BASE_URL}/detail/{sub_id}.html"
        )
        archive_bytes, raw_filename = await asyncio.to_thread(zimuku_provider.download, candidate)
    else:
        logger.error(f"[Download] Unknown provider requested: {provider}")
        raise HTTPException(status_code=400, detail=f"Unknown subtitle provider: {provider}")

    if not archive_bytes:
        logger.warning(f"[Download] Failed to download {sub_id} from {provider} (requested filename: '{filename}')")
        raise HTTPException(status_code=404, detail="Upstream subtitle download failed")

    target_ep = episode_num if episode_num > 0 else None

    # 3. Check if upstream file is directly a subtitle (SRT, ASS, VTT) rather than an archive
    if is_direct_subtitle(archive_bytes, raw_filename):
        logger.info(
            f"[Download] Upstream file '{raw_filename}' is directly a subtitle file "
            f"({len(archive_bytes)} bytes). Bypassing archive unpack."
        )
        sub_bytes, sub_name = archive_bytes, raw_filename
    else:
        logger.info(
            f"[Download] Successfully fetched archive '{raw_filename}' from {provider} ({len(archive_bytes)} bytes). "
            f"Extracting target episode {target_ep}..."
        )
        sub_bytes, sub_name = SubtitleExtractor.extract_best_subtitle(
            archive_bytes,
            raw_filename,
            episode=target_ep
        )

        if not sub_bytes:
            logger.error(
                f"[Download] Failed to unpack archive '{raw_filename}' for {provider}:{sub_id} (target ep={target_ep})"
            )
            raise HTTPException(status_code=502, detail="Failed to extract subtitle from archive")
        else:
            logger.info(f"[Download] Extracted best candidate '{sub_name}' ({len(sub_bytes)} bytes) from archive.")

    # 4. Clean tags, remove ASS drawing/positioning codes, and normalize to UTF-8 SRT
    logger.info(f"[Download] Normalizing '{sub_name}' ({len(sub_bytes)} bytes) to clean UTF-8 SRT...")
    cleaned_srt = SubtitleCleaner.normalize_to_utf8_srt(sub_bytes, sub_name)
    cues_count = cleaned_srt.count(b"-->")

    # Validate that we actually produced valid SRT subtitle content
    if len(cleaned_srt.strip()) < 20 or cues_count == 0:
        logger.error(
            f"[Download] Normalization yielded invalid SRT for {provider}:{sub_id} "
            f"(len={len(cleaned_srt)}, cues={cues_count}, raw_file='{sub_name}')"
        )
        raise HTTPException(status_code=502, detail="Subtitle extraction resulted in invalid SRT format")

    logger.info(
        f"[Download] Subtitle ready: {len(cleaned_srt)} bytes, {cues_count} cues. Saving to cache '{cache_filename}'."
    )

    # 5. Save to disk cache for subsequent requests
    try:
        cache_manager.save_subtitle(cache_filename, cleaned_srt)
    except Exception as e:
        logger.error(f"[Download] Error caching {cache_filename}: {e}")

    # 6. Stream directly to Stremio player
    return Response(
        content=cleaned_srt,
        media_type="text/plain; charset=utf-8",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "public, max-age=86400",
        }
    )
