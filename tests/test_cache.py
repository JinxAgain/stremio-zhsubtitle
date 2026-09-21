"""Unit tests for cache manager."""

import tempfile
from pathlib import Path
from app.cache.manager import CacheManager


def test_search_cache_storage_and_retrieval():
    with tempfile.TemporaryDirectory() as td:
        cm = CacheManager(cache_dir=Path(td))
        key = "movie:tt0903747:0:0:bilingual:all:8"
        sample_data = [{"id": "sub_1", "lang": "chi", "url": "https://example.com/1.srt"}]

        assert cm.get_search_results(key) is None

        cm.set_search_results(key, sample_data)
        retrieved = cm.get_search_results(key)
        assert retrieved == sample_data


def test_disk_subtitle_cache():
    with tempfile.TemporaryDirectory() as td:
        cm = CacheManager(cache_dir=Path(td))
        filename = "test_sub.srt"
        content = b"1\n00:00:01,000 --> 00:00:02,000\nHello"

        assert not cm.has_subtitle(filename)
        cm.save_subtitle(filename, content)
        assert cm.has_subtitle(filename)
        assert cm.get_subtitle(filename) == content
