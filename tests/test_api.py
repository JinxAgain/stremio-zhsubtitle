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
    assert data["name"] == "Chinese Subtitles (Zimuku & SubHD)"
    assert data["version"] == "1.1.0"



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
    """Verify format_subtitle_filename creates informative titles with release specs."""
    from app.api.subtitles import format_subtitle_filename, format_subtitle_info
    from app.providers.base import SubtitleCandidate, SubtitleTags, VideoQueryMeta

    cand = SubtitleCandidate(
        id="123",
        provider="subhd",
        title="Community.S01E14.1080p.BluRay.x264.srt",
        page_url="http://example.com",
        tags=SubtitleTags(bilingual=True)
    )
    query = VideoQueryMeta(imdb_id="tt1439629", media_type="series", season=1, episode=14, title="Community")
    label, fn = format_subtitle_info(cand, query)

    assert "[Bilingual.SubHD]" in fn
    assert fn.endswith(".srt")
    assert "Community.S01E14" in fn
    assert "BluRay" in fn
    assert "1080p" in fn
    assert "x264" in fn
    assert all(ord(c) < 128 for c in fn)

    # Label should follow OpenSubtitles v3 format with middle dot
    assert "Community · S01E14" in label
    assert "BluRay" in label
    assert "1080p" in label
    assert "x264" in label
    assert "[Bilingual SubHD]" in label

    # Test candidate with Chinese title
    cand_cn = SubtitleCandidate(
        id="456",
        provider="zimuku",
        title="废柴联盟 第一季 (全25集 WEB-DL版 中英双字) Community.S01.WEB.zip",
        page_url="http://example.com",
        tags=SubtitleTags(bilingual=True)
    )
    fn_cn = format_subtitle_filename(cand_cn, query)
    assert "[Bilingual.Zimuku]" in fn_cn
    assert "Community" in fn_cn
    assert "WEB-DL" in fn_cn
    assert "zip" not in fn_cn
    assert all(ord(c) < 128 for c in fn_cn)


def test_parse_media_id_tmdb():
    from app.api.subtitles import parse_media_id, extract_meta_from_filename

    # Movie with tmdb prefix
    meta_m = parse_media_id("movie", "tmdb:12345")
    assert meta_m.imdb_id == "tmdb:12345"
    assert meta_m.season is None
    assert meta_m.episode is None

    # Series with tmdb prefix and season/episode
    meta_s = parse_media_id("series", "tmdb:67890:2:5")
    assert meta_s.imdb_id == "tmdb:67890"
    assert meta_s.season == 2
    assert meta_s.episode == 5

    # Filename extraction fallback
    title, year, season, ep = extract_meta_from_filename("The.End.of.Oak.Street.2026.2160p.AMZN.WEB-DL.mkv")
    assert title == "The End of Oak Street"
    assert year == 2026

    title_tv, _, s_tv, ep_tv = extract_meta_from_filename("Community.S02E06.1080p.mkv")
    assert title_tv == "Community"
    assert s_tv == 2
    assert ep_tv == 6


def test_manifest_no_id_prefixes_restriction():
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = response.json()
    sub_res = [r for r in data["resources"] if r.get("name") == "subtitles"][0]
    assert "idPrefixes" not in sub_res


def test_manifest_head_method():
    """Verify HEAD /manifest.json returns 200 OK for UptimeRobot monitoring."""
    resp = client.head("/manifest.json")
    assert resp.status_code == 200


def test_subtitles_url_with_newline():
    """Verify multiline stream filename containing %0A does not 404 in Starlette routing."""
    url = (
        "/subtitles/series/tt4337894:1:4/filename=%F0%9F%93%81%C2%A0You%2C%20Me%20and%20the%20Apocalypse"
        "%20S01E04%201080p%20WEB-DL%20DD%2B%205.1%20x264-TrollHD.mkv%0A%E2%9A%99%EF%B8%8F%201080P"
        "%20%E2%80%A2%20%F0%9F%92%BE%202.9%20GB%20%F0%9F%93%81%C2%A0You%2C%20Me%20and%20the%20Apocalypse"
        "%20WEB-DL%201080p%20TROLLHD.json"
    )
    resp = client.get(url)
    assert resp.status_code == 200
    assert "subtitles" in resp.json()


def test_season_pack_scoring_priority():
    """Verify full season packs receive top priority and are not penalized."""
    from app.core.scorer import SubtitleScorer
    from app.providers.base import SubtitleCandidate, SubtitleTags, VideoQueryMeta

    query = VideoQueryMeta(imdb_id="tt4337894", media_type="series", season=1, episode=4, title="You, Me and the Apocalypse")

    c_pack = SubtitleCandidate(
        id="pack",
        provider="subhd",
        title="You.Me.and.the.Apocalypse.S01.1080p.WEBRip.DD5.1.x264-TrollHD",
        page_url="https://subhd.tv/a/pack",
        tags=SubtitleTags(bilingual=True)
    )
    c_single = SubtitleCandidate(
        id="single",
        provider="subhd",
        title="You.Me.and.the.Apocalypse.S01E04.720p.HDTV.x264",
        page_url="https://subhd.tv/a/single",
        tags=SubtitleTags(bilingual=True)
    )

    ranked = SubtitleScorer.rank_candidates([c_single, c_pack], query)
    assert ranked[0].id == "pack"


def test_pick_best_file_season_pack_and_variants():
    """Verify picking target episode from season pack and bilingual SRT over english-only."""
    from app.core.extractor import SubtitleExtractor

    # 1. Season pack
    files = [
        "You, Me and the Apocalypse S01E01.zh-en.srt",
        "You, Me and the Apocalypse S01E04.zh-en.srt",
        "You, Me and the Apocalypse S01E10.zh-en.srt"
    ]
    assert SubtitleExtractor.pick_best_file(files, episode=4) == "You, Me and the Apocalypse S01E04.zh-en.srt"

    # 2. Multi-variant archive: avoid english-only and prefer bilingual SRT
    variants = [
        "Walking.Dead.S09E09.1080p.chs.eng.英文.srt",
        "Walking.Dead.S09E09.1080p.chs.eng.繁体.ass",
        "Walking.Dead.S09E09.1080p.chs.eng.简体&英文.srt",
        "Walking.Dead.S09E09.1080p.chs.eng.繁体&英文.ass"
    ]
    best = SubtitleExtractor.pick_best_file(variants, episode=9, prefer_bilingual=True)
    assert best == "Walking.Dead.S09E09.1080p.chs.eng.简体&英文.srt"


def test_empty_cache_invalidation_when_filename_provided():
    """Verify that empty cache is rejected when filename or title is present."""
    from app.api.subtitles import _is_cache_valid
    from app.providers.base import VideoQueryMeta

    # Empty cache without filename/title is valid (normal empty result)
    meta_empty = VideoQueryMeta(imdb_id="tt12345", media_type="movie", filename="", title="")
    assert _is_cache_valid([], meta_empty) is True

    # Empty cache WITH filename must be invalidated so that the search can run
    meta_with_fn = VideoQueryMeta(
        imdb_id="tt12345",
        media_type="movie",
        filename="Movie.2026.1080p.mkv",
        title="Movie"
    )
    assert _is_cache_valid([], meta_with_fn) is False


def test_query_building_hygiene_for_tmdb_and_filename():
    """Ensure providers never query 'tmdb:...' and fallback to filename when title is empty."""
    from app.providers.zimuku import ZimukuProvider
    from app.providers.subhd import SubhdProvider
    from app.providers.base import VideoQueryMeta

    zimuku = ZimukuProvider()
    subhd = SubhdProvider()

    # When imdb_id is tmdb:1083381 and title is empty but filename is present
    meta = VideoQueryMeta(
        imdb_id="tmdb:1083381",
        media_type="movie",
        title="",
        filename="Backrooms-2026-1080p.mkv"
    )

    z_queries = zimuku._build_queries(meta)
    s_queries = subhd._build_queries(meta)

    # Neither provider should query 'tmdb:1083381'
    assert "tmdb:1083381" not in z_queries
    assert "tmdb:1083381" not in s_queries

    # Both should have extracted the title from filename
    assert "Backrooms" in z_queries
    assert "Backrooms" in s_queries


def test_imdb_priority_and_year_mismatch_penalty():
    """Verify IMDb ID is prioritized first and conflicting years are heavily penalized."""
    from app.providers.zimuku import ZimukuProvider
    from app.providers.subhd import SubhdProvider
    from app.providers.base import VideoQueryMeta, SubtitleCandidate, SubtitleTags
    from app.core.scorer import SubtitleScorer

    zimuku = ZimukuProvider()
    subhd = SubhdProvider()

    meta = VideoQueryMeta(
        imdb_id="tt31349844",
        media_type="movie",
        title="Runner",
        year=2026
    )

    z_queries = zimuku._build_queries(meta)
    s_queries = subhd._build_queries(meta)

    # 1. IMDb ID MUST be the first query in both providers
    assert z_queries[0] == "tt31349844"
    assert s_queries[0] == "tt31349844"

    # 2. Year mismatch penalty test
    c_exact = SubtitleCandidate(
        id="c1",
        provider="subhd",
        title="Runner.2026.1080p.WEB-DL",
        page_url="",
        tags=SubtitleTags(imdb_matched=True, bilingual=True)
    )
    c_wrong_year = SubtitleCandidate(
        id="c2",
        provider="zimuku",
        title="Blade.Runner.2049.2017.1080p.BluRay",
        page_url="",
        tags=SubtitleTags(imdb_matched=False, bilingual=True)
    )

    ranked = SubtitleScorer.rank_candidates([c_wrong_year, c_exact], meta)
    assert ranked[0].id == "c1"
    assert c_exact.score > 1500
    assert c_wrong_year.score < 0


def test_tv_season_mismatch_rejection():
    """Verify is_episode_match strictly rejects candidates from different seasons."""
    from app.providers.base import is_episode_match

    # Ted Lasso S04E09 vs S01E09 (exact user case)
    match, is_pack = is_episode_match(
        "[zmk.pw]足球教练 第1季第9集（Ted.Lasso S01E09）.zip",
        season=4,
        episode=9
    )
    assert match is False
    assert is_pack is False

    # Ted Lasso S04E09 vs S04E09
    match, is_pack = is_episode_match(
        "Ted.Lasso.S04E09.1080p.WEB-DL.srt",
        season=4,
        episode=9
    )
    assert match is True
    assert is_pack is False

    # Season 4 Pack
    match, is_pack = is_episode_match(
        "Ted Lasso 第四季全集",
        season=4,
        episode=9
    )
    assert match is True
    assert is_pack is True

    # Season 1 Pack when query is Season 4 -> Rejected
    match, is_pack = is_episode_match(
        "Ted Lasso 第一季全集",
        season=4,
        episode=9
    )
    assert match is False
    assert is_pack is False


def test_zimuku_series_season_gt_1_skips_imdb_id():
    """Verify Zimuku does NOT search IMDb ID when TV series season > 1."""
    from app.providers.zimuku import ZimukuProvider
    from app.providers.base import VideoQueryMeta

    zimuku = ZimukuProvider()
    meta_s4 = VideoQueryMeta(
        imdb_id="tt10986410",
        media_type="series",
        season=4,
        episode=9,
        title="Ted Lasso"
    )
    queries = zimuku._build_queries(meta_s4)

    # Must be ONLY "Ted Lasso 第四季" (no S04, no tt10986410)
    assert queries == ["Ted Lasso 第四季"]

    # Season 1 CAN use IMDb ID
    meta_s1 = VideoQueryMeta(
        imdb_id="tt10986410",
        media_type="series",
        season=1,
        episode=1,
        title="Ted Lasso"
    )
    queries_s1 = zimuku._build_queries(meta_s1)
    assert "tt10986410" in queries_s1


def test_scorer_penalizes_season_mismatch():
    """Verify SubtitleScorer heavily penalizes candidates with conflicting seasons."""
    from app.providers.base import VideoQueryMeta, SubtitleCandidate, SubtitleTags
    from app.core.scorer import SubtitleScorer

    meta = VideoQueryMeta(
        imdb_id="tt10986410",
        media_type="series",
        season=4,
        episode=9,
        title="Ted Lasso"
    )

    c_s1 = SubtitleCandidate(
        id="c_s1",
        provider="zimuku",
        title="[zmk.pw]足球教练 第1季第9集（Ted.Lasso S01E09）.zip",
        page_url="",
        tags=SubtitleTags(imdb_matched=True, bilingual=True)
    )

    c_s4 = SubtitleCandidate(
        id="c_s4",
        provider="subhd",
        title="Ted.Lasso.S04E09.1080p.WEB-DL.[Bilingual.SubHD].srt",
        page_url="",
        tags=SubtitleTags(imdb_matched=False, bilingual=True)
    )

    ranked = SubtitleScorer.rank_candidates([c_s1, c_s4], meta)
    assert ranked[0].id == "c_s4"
    assert c_s1.score < 0





