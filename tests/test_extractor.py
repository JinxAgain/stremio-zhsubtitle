"""Unit tests for archive extraction and episode selection."""

import io
import zipfile
import pytest
from app.core.extractor import SubtitleExtractor, fix_archive_filename


def test_pick_best_file_episode_matching():
    files = [
        "Breaking.Bad.S02E01.chs.srt",
        "Breaking.Bad.S02E06.chs.srt",
        "Breaking.Bad.S02E06.chs&eng.ass",
        "Breaking.Bad.S02E10.chs.srt"
    ]
    # For episode 6, bilingual .ass should score highest
    best = SubtitleExtractor.pick_best_file(files, episode=6, prefer_bilingual=True)
    assert best == "Breaking.Bad.S02E06.chs&eng.ass"


def test_pick_best_file_language_priority():
    files = [
        "Movie.2024.cht.srt",
        "Movie.2024.chs.srt",
        "Movie.2024.chs&eng.ass"
    ]
    # Default bilingual first
    best_bilingual = SubtitleExtractor.pick_best_file(files, prefer_bilingual=True)
    assert best_bilingual == "Movie.2024.chs&eng.ass"

    # Traditional priority
    best_cht = SubtitleExtractor.pick_best_file(files, prefer_bilingual=False, prefer_traditional=True)
    assert best_cht == "Movie.2024.cht.srt"


def test_zip_in_memory_extraction():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("S01E01.chs.srt", "1\n00:00:01,000 --> 00:00:02,000\nEpisode 1\n")
        zf.writestr("S01E02.chs.srt", "1\n00:00:01,000 --> 00:00:02,000\nEpisode 2\n")

    content, name = SubtitleExtractor.extract_best_subtitle(
        buf.getvalue(),
        "test.zip",
        episode=2
    )
    assert name == "S01E02.chs.srt"
    assert b"Episode 2" in content


def test_is_episode_match_season_pack_vs_individual():
    from app.providers.base import is_episode_match

    # Season pack for Season 3 matches E04 as a pack
    matched, is_pack = is_episode_match("The.Thick.Of.It.S03.1080p.AMZN.WEB-DL.DD2.0.x264-ARiN", season=3, episode=4)
    assert matched is True
    assert is_pack is True

    # S03E07 should NOT match E04
    matched, is_pack = is_episode_match("The Thick of It S03E07", season=3, episode=4)
    assert matched is False
    assert is_pack is False

    # S03E04 should match E04 as non-pack
    matched, is_pack = is_episode_match("The.Thick.of.It.S03E04.WEB-DL", season=3, episode=4)
    assert matched is True
    assert is_pack is False


def test_zimuku_unhealthy_cooldown():
    from app.providers.zimuku import ZimukuProvider

    domain = "https://failing-test-mirror.com"
    assert ZimukuProvider.is_domain_healthy(domain) is True

    ZimukuProvider.mark_domain_unhealthy(domain, cooldown_secs=30)
    assert ZimukuProvider.is_domain_healthy(domain) is False


def test_subhd_cooldown_and_rate_limit():
    from app.providers.subhd import SubhdProvider

    domain = "https://failing-subhd-mirror.com"
    assert SubhdProvider.is_domain_healthy(domain) is True

    SubhdProvider.mark_domain_unhealthy(domain, cooldown_secs=30)
    assert SubhdProvider.is_domain_healthy(domain) is False

    # Test rate limit trigger
    SubhdProvider._rate_limited_until = 0.0
    assert SubhdProvider.is_rate_limited() is False

    SubhdProvider.mark_rate_limited(duration_seconds=300, reason="Test 403")
    assert SubhdProvider.is_rate_limited() is True
    # Reset for other tests
    SubhdProvider._rate_limited_until = 0.0


