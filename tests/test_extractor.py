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
