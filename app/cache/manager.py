"""Two-tier cache manager: SQLite for search results and Disk for cleaned subtitles."""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages SQLite search query cache and on-disk cleaned subtitle files."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or settings.CACHE_DIR
        self.db_path = self.cache_dir / "search_cache.db"
        self.subtitles_dir = self.cache_dir / "subtitles"
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite schema if not present."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.subtitles_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    query_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_search_cache_created 
                ON search_cache(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    async def get_flight_lock(self, key: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for single-flight upstream query coalescence."""
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    # --- Search Cache Methods ---

    def get_search_results(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve cached search results if created within TTL."""
        ttl_seconds = settings.CACHE_TTL_HOURS * 3600
        now = int(time.time())

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT data_json, created_at FROM search_cache WHERE query_key = ?",
                (key,)
            )
            row = cursor.fetchone()
            if row:
                data_json, created_at = row
                if now - created_at < ttl_seconds:
                    return json.loads(data_json)
                else:
                    # Expired, clean up record
                    conn.execute("DELETE FROM search_cache WHERE query_key = ?", (key,))
                    conn.commit()
        except Exception as e:
            logger.warning(f"Error reading search cache for {key}: {e}")
        finally:
            conn.close()

        return None

    def set_search_results(self, key: str, data: List[Dict[str, Any]]) -> None:
        """Store search results in SQLite with current timestamp."""
        now = int(time.time())
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO search_cache (query_key, data_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(query_key) DO UPDATE SET
                    data_json = excluded.data_json,
                    created_at = excluded.created_at
                """,
                (key, json.dumps(data, ensure_ascii=False), now)
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Error writing search cache for {key}: {e}")
        finally:
            conn.close()

    # --- Disk Subtitle Cache Methods ---

    def get_subtitle_path(self, filename: str) -> Path:
        """Return absolute path to cached subtitle file."""
        return self.subtitles_dir / filename

    def has_subtitle(self, filename: str) -> bool:
        """Check if cleaned subtitle exists on disk."""
        path = self.get_subtitle_path(filename)
        return path.is_file() and path.stat().st_size > 0

    def get_subtitle(self, filename: str) -> Optional[bytes]:
        """Read subtitle content from disk."""
        path = self.get_subtitle_path(filename)
        if path.is_file():
            try:
                return path.read_bytes()
            except Exception as e:
                logger.error(f"Error reading subtitle file {filename}: {e}")
        return None

    def save_subtitle(self, filename: str, content: Any) -> Path:
        """Save cleaned UTF-8 subtitle bytes or string to disk."""
        path = self.get_subtitle_path(filename)
        try:
            if isinstance(content, str):
                path.write_bytes(content.encode("utf-8"))
            elif isinstance(content, (bytes, bytearray)):
                path.write_bytes(content)
            else:
                path.write_bytes(bytes(content))
            return path
        except Exception as e:
            logger.error(f"Error saving subtitle file {filename}: {e}")
            raise

    def clean_expired(self) -> None:
        """Purge expired SQLite search entries and maintain disk space bounds."""
        ttl_seconds = settings.CACHE_TTL_HOURS * 3600
        cutoff = int(time.time()) - ttl_seconds

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM search_cache WHERE created_at < ?", (cutoff,))
            conn.commit()
        except Exception as e:
            logger.warning(f"Error cleaning expired search cache: {e}")
        finally:
            conn.close()


cache_manager = CacheManager()
