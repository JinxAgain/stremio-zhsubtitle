"""Cinemeta client for resolving IMDb IDs to media metadata."""

import logging
import re
from typing import Optional, Tuple

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class CinemetaClient:
    """Fetches media metadata (title, release year, aliases) from Cinemeta and TMDB."""

    @classmethod
    async def resolve_metadata(cls, media_type: str, imdb_id: str) -> Tuple[str, Optional[int]]:
        """
        Query Cinemeta or TMDB API to resolve ID to (title, year).
        Returns ("", None) if resolution fails.
        """
        title, year, _ = await cls.resolve_media_info(media_type, imdb_id)
        return title, year

    @classmethod
    async def resolve_media_info(
        cls, media_type: str, id_str: str
    ) -> Tuple[str, Optional[int], Optional[str]]:
        """
        Resolve media metadata for IMDb IDs ('tt...') and TMDB IDs ('tmdb:...').
        Returns (title, year, resolved_imdb_id).
        """
        if not id_str:
            return "", None, None

        clean_type = "series" if media_type == "series" else "movie"

        # 1. Standard IMDb ID via Cinemeta
        if id_str.startswith("tt"):
            url = f"{settings.CINEMETA_URL}/meta/{clean_type}/{id_str}.json"
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json().get("meta", {})
                        name = data.get("name", "").strip()
                        year_val = data.get("year")
                        year = None
                        if year_val:
                            m = re.search(r"(\d{4})", str(year_val))
                            if m:
                                year = int(m.group(1))
                        return name, year, id_str
            except Exception as e:
                logger.debug(f"Cinemeta resolution error for {id_str}: {e}")
            return "", None, id_str

        # 2. TMDB ID resolution (e.g. tmdb:1083381 or tmdb:62456)
        if id_str.startswith("tmdb:"):
            tmdb_id_part = id_str.split(":")[1] if ":" in id_str else id_str

            # Try TMDB Addon first
            tmdb_addon_url = (
                f"https://94c8cb9f702d-tmdb-addon.baby-beamup.club/meta/{clean_type}/tmdb:{tmdb_id_part}.json"
            )
            try:
                async with httpx.AsyncClient(timeout=2.5) as client:
                    resp = await client.get(tmdb_addon_url)
                    if resp.status_code == 200:
                        meta = resp.json().get("meta", {})
                        name = meta.get("name", "").strip()
                        resolved_imdb = meta.get("imdb_id")
                        year_val = meta.get("year") or meta.get("releaseInfo")
                        year = None
                        if year_val:
                            m = re.search(r"(\d{4})", str(year_val))
                            if m:
                                year = int(m.group(1))
                        if name:
                            return name, year, resolved_imdb
            except Exception as e:
                logger.debug(f"TMDB addon lookup failed for {id_str}: {e}")

            # Fallback: Scrape title and year from themoviedb.org
            tmdb_web_type = "tv" if media_type == "series" else "movie"
            tmdb_web_url = f"https://www.themoviedb.org/{tmdb_web_type}/{tmdb_id_part}"
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    )
                }
                async with httpx.AsyncClient(timeout=3.5, headers=headers, follow_redirects=True) as client:
                    resp = await client.get(tmdb_web_url)
                    if resp.status_code == 200:
                        m_title = re.search(
                            r"<title>\s*(.*?)\s*(?:\((?:TV Series\s*)?(\d{4})\))?\s*&#8212;\s*The Movie Database",
                            resp.text,
                        )
                        if m_title:
                            name = m_title.group(1).strip()
                            year = int(m_title.group(2)) if m_title.group(2) else None
                            return name, year, None
            except Exception as e:
                logger.debug(f"TheMovieDb web fallback failed for {id_str}: {e}")

        return "", None, None
