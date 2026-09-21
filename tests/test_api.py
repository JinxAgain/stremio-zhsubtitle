"""Unit tests for FastAPI Stremio routes and manifest."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_manifest_default():
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "org.stremio.zhsubtitle"
    assert "subtitles" in [r["name"] for r in data["resources"]]
    assert "movie" in data["types"]
    assert "series" in data["types"]
    assert data["behaviorHints"]["configurable"] is True


def test_manifest_configured():
    response = client.get("/eyJsYW5nIjoiY2hzIiwic291cmNlIjoiYWxsIn0/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert "[简]" in data["name"]


def test_configure_page():
    response = client.get("/configure")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "中文字幕聚合插件" in response.text


def test_subtitles_route_with_extra():
    # Should not return 404
    response = client.get("/subtitles/movie/tt29355505/videoSize%3D19574313452.json")
    assert response.status_code == 200
    assert "subtitles" in response.json()


def test_subtitles_series_url_encoded():
    # Should not return 404
    response = client.get(
        "/subtitles/series/tt1439629%3A1%3A14/filename%3DCommunity.S01E14.1080p.mkv%26videoSize%3D1342177280.json"
    )
    assert response.status_code == 200
    assert "subtitles" in response.json()


def test_download_endpoint_returns_srt():
    """Verify that /subtitles/dl route is handled by download_router and returns SRT, not JSON."""
    from app.cache.manager import cache_manager

    test_sub_fn = "subhd_unit_test_999_0.srt"
    test_srt_content = "1\n00:00:01,000 --> 00:00:04,000\nUnit Test Subtitle\n"
    cache_manager.save_subtitle(test_sub_fn, test_srt_content)

    resp = client.get("/subtitles/dl/subhd/unit_test_999/0/[双语.SubHD].Test.Movie.srt")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "subtitles" not in resp.headers["content-type"]
    assert resp.text == test_srt_content
    # Content-Disposition is intentionally omitted so download managers like IDM do not hijack video player streaming
    assert "content-disposition" not in resp.headers
    assert resp.headers.get("access-control-allow-origin") == "*"
    assert "public" in resp.headers.get("cache-control", "")


def test_subtitles_invalid_media_type():
    """Invalid media types should return 404 and not clash with other routes."""
    resp = client.get("/subtitles/invalid_type/tt1234567.json")
    assert resp.status_code == 404


def test_format_subtitle_filename():
    """Verify format_subtitle_filename creates informative titles."""
    from app.api.subtitles import format_subtitle_filename
    from app.providers.base import SubtitleCandidate, SubtitleTags, VideoQueryMeta

    cand = SubtitleCandidate(
        id="123",
        provider="subhd",
        title="Community.S01E14.1080p.BluRay.x264.srt",
        page_url="http://example.com",
        tags=SubtitleTags(bilingual=True)
    )
    query = VideoQueryMeta(imdb_id="tt1439629", media_type="series", season=1, episode=14, title="Community")
    fn = format_subtitle_filename(cand, query)
    assert fn.startswith("[Bilingual.SubHD]")
    assert fn.endswith(".srt")
    assert "0.srt" not in fn
    assert "Community.S01E14" in fn
    # Must be 100% ASCII
    assert all(ord(c) < 128 for c in fn)

    # Test candidate with Chinese title
    cand_cn = SubtitleCandidate(
        id="456",
        provider="zimuku",
        title="废柴联盟 第一季 (全25集 WEB-DL版 中英双字) Community.S01.WEB.zip",
        page_url="http://example.com",
        tags=SubtitleTags(bilingual=True)
    )
    fn_cn = format_subtitle_filename(cand_cn, query)
    assert fn_cn.startswith("[Bilingual.Zimuku]")
    assert "Community" in fn_cn
    assert "zip" not in fn_cn
    assert all(ord(c) < 128 for c in fn_cn)

