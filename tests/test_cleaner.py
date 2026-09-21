"""Unit tests for subtitle normalization and cleaning engine."""

import pytest
from app.core.cleaner import SubtitleCleaner


def test_ass_time_conversion():
    assert SubtitleCleaner.ass_time_to_srt_time("0:01:23.45") == "00:01:23,450"
    assert SubtitleCleaner.ass_time_to_srt_time("1:02:03.456") == "01:02:03,456"
    assert SubtitleCleaner.ass_time_to_srt_time("0:00:05.1") == "00:00:05,100"


def test_clean_dialogue_text():
    raw_text = r"{\pos(192,240)\b1}Hello\NWorld{\p1}m 0 0 l 10 10{\p0}"
    cleaned = SubtitleCleaner.clean_dialogue_text(raw_text)
    assert r"{\pos" not in cleaned
    assert r"{\p1}" not in cleaned
    assert cleaned == "Hello\nWorld"


def test_ass_to_srt():
    sample_ass = r"""[Script Info]
Title: Test ASS
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.20,0:00:03.50,Default,,0,0,0,,{\pos(100,200)}First line\NSecond line
Comment: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,Should be ignored
Dialogue: 0,0:00:04.00,0:00:06.80,Default,,0,0,0,,{\c&H00FFFF&}Third line
"""
    srt_result = SubtitleCleaner.ass_to_srt(sample_ass)
    assert "1\n00:00:01,200 --> 00:00:03,500\nFirst line\nSecond line" in srt_result
    assert "Should be ignored" not in srt_result
    assert "2\n00:00:04,000 --> 00:00:06,800\nThird line" in srt_result


def test_vtt_to_srt():
    sample_vtt = """WEBVTT

00:01.000 --> 00:03.500
Subtitle line one

00:04.200 --> 00:06.800
Subtitle line two
"""
    srt_result = SubtitleCleaner.vtt_to_srt(sample_vtt)
    assert "00:00:01,000 --> 00:00:03,500" in srt_result
    assert "Subtitle line one" in srt_result
    assert "00:00:04,200 --> 00:00:06,800" in srt_result


def test_charset_detection_and_decoding():
    gbk_bytes = "你好，世界！".encode("gbk")
    decoded, enc = SubtitleCleaner.detect_and_decode(gbk_bytes)
    assert decoded == "你好，世界！"
    assert enc in ("gbk", "gb18030")

    utf8_bom = b"\xef\xbb\xbf" + "测试中文字幕".encode("utf-8")
    decoded_bom, enc_bom = SubtitleCleaner.detect_and_decode(utf8_bom)
    assert decoded_bom == "测试中文字幕"
    assert enc_bom == "utf-8-sig"
