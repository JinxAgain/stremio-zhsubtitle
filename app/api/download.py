"""On-demand subtitle download, extraction, normalization, and streaming endpoint."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from ..cache.manager import cache_manager
from ..config import settings
from ..core.cleaner import SubtitleCleaner
from ..core.extractor import SubtitleExtractor
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
    out_filename = filename if filename.endswith(".srt") else f"{filename or cache_filename}.srt"

    # 1. Fast path: Return cached SRT from disk if present (<5ms)
    if cache_manager.has_subtitle(cache_filename):
        content = cache_manager.get_subtitle(cache_filename)
        if content:
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
        raise HTTPException(status_code=400, detail=f"Unknown subtitle provider: {provider}")

    if not archive_bytes:
        logger.warning(f"[Download] Failed to download {sub_id} from {provider}")
        raise HTTPException(status_code=404, detail="Upstream subtitle download failed")

    # 3. Unpack archive and extract matching episode file
    target_ep = episode_num if episode_num > 0 else None
    sub_bytes, sub_name = SubtitleExtractor.extract_best_subtitle(
        archive_bytes,
        raw_filename,
        episode=target_ep
    )

    if not sub_bytes:
        # Check if the content is binary archive rather than direct subtitle file
        lower_name = raw_filename.lower()
        is_archive = (
            lower_name.endswith((".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"))
            or archive_bytes[:4] in (b"Rar!", b"PK\x03\x04", b"7z\xbc\xaf")
        )
        if is_archive:
            logger.error(f"[Download] Failed to unpack archive {raw_filename} ({provider}:{sub_id})")
            raise HTTPException(status_code=502, detail="Failed to extract subtitle from archive")
        sub_bytes, sub_name = archive_bytes, raw_filename

    # 4. Clean tags, remove ASS drawing/positioning codes, and normalize to UTF-8 SRT
    cleaned_srt = SubtitleCleaner.normalize_to_utf8_srt(sub_bytes, sub_name)

    # Validate that we actually produced valid SRT subtitle content
    if len(cleaned_srt.strip()) < 20 or b"-->" not in cleaned_srt:
        logger.error(f"[Download] Normalization yielded invalid SRT for {provider}:{sub_id}")
        raise HTTPException(status_code=502, detail="Subtitle extraction resulted in invalid SRT format")

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
