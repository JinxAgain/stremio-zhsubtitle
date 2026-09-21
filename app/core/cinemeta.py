"""Cinemeta client for resolving IMDb IDs to media metadata."""

import logging
import re
from typing import Optional, Tuple

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class CinemetaClient:
    """Fetches media metadata (title, release year, aliases) from Stremio's Cinemeta service."""

    @classmethod
    async def resolve_metadata(cls, media_type: str, imdb_id: str) -> Tuple[str, Optional[int]]:
        """
        Query Cinemeta API to resolve IMDb ID to (title, year).
        Returns ("", None) if resolution fails.
        """
        if not imdb_id or not imdb_id.startswith("tt"):
            return "", None

        clean_type = "series" if media_type == "series" else "movie"
        url = f"{settings.CINEMETA_URL}/meta/{clean_type}/{imdb_id}.json"

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
                    return name, year
        except Exception as e:
            logger.debug(f"Cinemeta resolution error for {imdb_id}: {e}")

        return "", None
