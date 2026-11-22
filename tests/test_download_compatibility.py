"""Regression tests for short URLs, HEAD parity and unavailable SubHD results."""

import pytest
from fastapi.testclient import TestClient

from app.api import download, subtitles
from app.cache.manager import CacheManager
from app.main import app
from app.providers.base import SubtitleCandidate, SubtitleTags, VideoQueryMeta

SRT = b"1\n00:00:01,000 --> 00:00:04,000\nExample subtitle\n"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    cache = CacheManager(tmp_path)
    monkeypatch.setattr(download, "cache_manager", cache)
    monkeypatch.setattr(subtitles, "cache_manager", cache)
    return cache


@pytest.mark.parametrize("suffix", ["1.srt", "1/Legacy.[Bilingual.Zimuku].srt"])
@pytest.mark.parametrize("cached", [True, False])
def test_head_matches_get_headers_with_empty_body(cache, monkeypatch, suffix, cached):
    if cached:
        cache.save_subtitle("zimuku_123_1.srt", SRT)
    else:
        monkeypatch.setattr(download.zimuku_provider, "download", lambda _: (SRT, "example.srt"))
    client = TestClient(app)
    path = f"/subtitles/dl/zimuku/123/{suffix}"
    head = client.head(path, headers={"Origin": "https://web.stremio.com"})
    get = client.get(path, headers={"Origin": "https://web.stremio.com"})
    assert head.status_code == get.status_code == 200
    assert head.content == b""
    assert get.content == SRT
    assert dict(head.headers) == dict(get.headers)
    assert int(head.headers["content-length"]) == len(SRT)


@pytest.mark.parametrize("suffix", ["1.srt", "1/Legacy.srt"])
def test_head_preserves_download_failure(cache, monkeypatch, suffix):
    monkeypatch.setattr(download.subhd_provider, "download", lambda _: (None, ""))
    response = TestClient(app).head(f"/subtitles/dl/subhd/unavailable/{suffix}")
    assert response.status_code == 404
    assert response.content == b""


def test_cache_accepts_short_paths_and_rejects_legacy_and_unavailable_subhd(cache):
    meta = VideoQueryMeta(imdb_id="tt5687612", media_type="series", season=1, episode=1)
    row = {"path": "/subtitles/dl/zimuku/123/1.srt", "label": "[Bilingual Zimuku]"}
    assert subtitles._is_cache_valid([row], meta)
    for path in ["/subtitles/dl/zimuku/123/1/old.[tag].srt",
                 "/subtitles/dl/zimuku/123/1/old.%5Btag%5D.srt"]:
        assert not subtitles._is_cache_valid([dict(row, path=path)], meta)
    subhd = dict(row, path="/subtitles/dl/subhd/cached/1.srt")
    assert not subtitles._is_cache_valid([subhd], meta)
    cache.save_subtitle("subhd_cached_1.srt", SRT)
    assert subtitles._is_cache_valid([subhd], meta)
    cache.get_subtitle_path("subhd_cached_1.srt").write_bytes(b"not a subtitle")
    assert not subtitles._is_cache_valid([subhd], meta)


def test_list_omits_uncached_subhd_before_ranking_and_reuses_safe_cache(cache, monkeypatch):
    async def metadata(*_):
        return "Fleabag", 2016, "tt5687612"

    monkeypatch.setattr(subtitles.CinemetaClient, "resolve_media_info", metadata)
    calls = []

    def candidate(provider, sid):
        return SubtitleCandidate(id=sid, provider=provider, title="Fleabag S01E01",
                                 page_url="", tags=SubtitleTags(bilingual=True))

    def subhd_search(_):
        calls.append("subhd")
        return [candidate("subhd", "broken"), candidate("subhd", "cached")]

    def zimuku_search(_):
        calls.append("zimuku")
        return [candidate("zimuku", "123")]

    monkeypatch.setattr(subtitles.subhd_provider, "search", subhd_search)
    monkeypatch.setattr(subtitles.zimuku_provider, "search", zimuku_search)
    monkeypatch.setattr(subtitles.subhd_provider, "download",
                        lambda _: pytest.fail("listing must not attempt SubHD downloads"))
    cache.save_subtitle("subhd_cached_1.srt", SRT)
    ranked_ids = []

    def rank(candidates, **_):
        ranked_ids.extend(c.id for c in candidates)
        return candidates

    monkeypatch.setattr(subtitles.SubtitleScorer, "rank_candidates", rank)
    client = TestClient(app)
    path = "/subtitles/series/tt5687612:1:1.json"
    rows = client.get(path).json()["subtitles"]
    assert ranked_ids == ["cached", "123"]
    assert {r["id"] for r in rows} == {"subhd_cached_1", "zimuku_123_1"}
    assert all(r["url"] == "http://testserver" + r["path"] for r in rows)
    assert all("[" not in r["url"] and "%" not in r["url"] for r in rows)
    assert all(r["path"].endswith("/1.srt") for r in rows)
    assert all("[Bilingual " in r["label"] for r in rows)
    assert client.get(path).json()["subtitles"] == rows
    assert len(calls) == 2  # second request used the short-path search cache
    cache.get_subtitle_path("subhd_cached_1.srt").unlink()
    rows = client.get(path).json()["subtitles"]
    assert [r["id"] for r in rows] == ["zimuku_123_1"]
    assert len(calls) == 4  # stale SubHD cache invalidated
