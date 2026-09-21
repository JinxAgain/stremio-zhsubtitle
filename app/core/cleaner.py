"""Subtitle normalization and ASS/SSA/VTT to clean UTF-8 SRT conversion engine."""

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

CHARSET_ENCODINGS = [
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "gbk",
    "big5",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "latin1"
]


class SubtitleCleaner:
    """Sanitizes subtitle text and converts various formats into standardized UTF-8 SRT."""

    @classmethod
    def detect_and_decode(cls, raw_bytes: bytes) -> Tuple[str, str]:
        """
        Probe subtitle byte encoding and return decoded string with detected encoding name.
        Always returns clean string without BOM.
        """
        if not raw_bytes:
            return "", "utf-8"

        # Check BOM explicitly
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            return raw_bytes[3:].decode("utf-8", errors="replace"), "utf-8-sig"
        elif raw_bytes.startswith(b"\xff\xfe"):
            return raw_bytes[2:].decode("utf-16-le", errors="replace"), "utf-16-le"
        elif raw_bytes.startswith(b"\xfe\xff"):
            return raw_bytes[2:].decode("utf-16-be", errors="replace"), "utf-16-be"

        for enc in CHARSET_ENCODINGS:
            try:
                decoded = raw_bytes.decode(enc)
                # Check for null bytes or typical decoded abnormalities
                if "\x00" in decoded and enc not in ("utf-16", "utf-16-le", "utf-16-be"):
                    continue
                return decoded, enc
            except (UnicodeDecodeError, LookupError):
                continue

        return raw_bytes.decode("utf-8", errors="replace"), "utf-8-fallback"

    @classmethod
    def normalize_to_utf8_srt(cls, raw_bytes: bytes, filename: str = "") -> bytes:
        """
        Normalize any subtitle content (.ass, .ssa, .srt, .vtt) into clean UTF-8 .srt bytes.
        """
        text, _ = cls.detect_and_decode(raw_bytes)
        lower_name = filename.lower()

        # Decide processor based on filename or content heuristics
        if lower_name.endswith((".ass", ".ssa")) or "[Script Info]" in text or "[Events]" in text:
            cleaned_srt = cls.ass_to_srt(text)
        elif lower_name.endswith(".vtt") or text.strip().startswith("WEBVTT"):
            cleaned_srt = cls.vtt_to_srt(text)
        else:
            cleaned_srt = cls.sanitize_srt(text)

        return cleaned_srt.encode("utf-8")

    @classmethod
    def ass_time_to_srt_time(cls, ass_time: str) -> str:
        """
        Convert ASS timestamp (e.g. '0:01:23.45' or '1:02:03.456') to SRT format ('00:01:23,450').
        """
        parts = ass_time.strip().split(":")
        if len(parts) == 2:
            hours = 0
            minutes, seconds_part = int(parts[0]), parts[1]
        elif len(parts) >= 3:
            hours, minutes, seconds_part = int(parts[0]), int(parts[1]), parts[2]
        else:
            return "00:00:00,000"

        if "." in seconds_part:
            sec_str, frac_str = seconds_part.split(".", 1)
        elif "," in seconds_part:
            sec_str, frac_str = seconds_part.split(",", 1)
        else:
            sec_str, frac_str = seconds_part, "00"

        seconds = int(sec_str)
        # Fraction can be 2 digits (centiseconds) or 3 digits (milliseconds)
        if len(frac_str) == 1:
            millis = int(frac_str) * 100
        elif len(frac_str) == 2:
            millis = int(frac_str) * 10
        elif len(frac_str) >= 3:
            millis = int(frac_str[:3])
        else:
            millis = 0

        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @classmethod
    def time_str_to_seconds(cls, srt_time: str) -> float:
        """Convert SRT timestamp ('00:01:23,450') to total seconds for sorting."""
        try:
            time_part, millis_part = srt_time.split(",")
            h, m, s = [int(v) for v in time_part.split(":")]
            return h * 3600 + m * 60 + s + int(millis_part) / 1000.0
        except Exception:
            return 0.0

    @classmethod
    def clean_dialogue_text(cls, text: str) -> str:
        """
        Strip ASS style tags, drawing commands, and normalize line breaks.
        """
        # Skip drawing commands {\p1}...{\p0}
        if re.search(r"\{\\p[1-9]\}.*?\{\\p0\}", text, flags=re.DOTALL):
            text = re.sub(r"\{\\p[1-9]\}.*?\{\\p0\}", "", text, flags=re.DOTALL)
        if re.search(r"\{\\p[1-9]\}", text):
            # If drawing command without reset, discard entire text
            return ""

        # Remove all ASS override tags like {\b1}, {\pos(x,y)}, etc.
        cleaned = re.sub(r"\{[^\}]*\}", "", text)

        # Normalize line break escapes
        cleaned = cleaned.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")

        # Remove HTML-style tags if any
        cleaned = re.sub(r"<[^>]+>", "", cleaned)

        # Clean trailing and leading whitespaces on each line
        lines = [line.strip() for line in cleaned.split("\n")]
        # Filter empty lines
        non_empty = [line for line in lines if line]
        return "\n".join(non_empty)

    @classmethod
    def ass_to_srt(cls, ass_content: str) -> str:
        """
        Parse ASS/SSA document and convert dialogue events into a valid sorted SRT file.
        """
        lines = ass_content.splitlines()
        in_events = False
        format_cols: Optional[List[str]] = None
        start_idx, end_idx, text_idx = 1, 2, 9  # Defaults for standard ASS

        cues: List[Tuple[float, str, str, str]] = []  # (start_sec, start_srt, end_srt, text)

        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue

            if line_strip.lower() == "[events]":
                in_events = True
                continue
            elif line_strip.startswith("[") and in_events:
                # Switched to another section
                break

            if in_events:
                if line_strip.lower().startswith("format:"):
                    raw_cols = line_strip.split(":", 1)[1]
                    format_cols = [c.strip().lower() for c in raw_cols.split(",")]
                    if "start" in format_cols:
                        start_idx = format_cols.index("start")
                    if "end" in format_cols:
                        end_idx = format_cols.index("end")
                    if "text" in format_cols:
                        text_idx = format_cols.index("text")
                    continue

                if line_strip.lower().startswith("dialogue:"):
                    # Split only up to text_idx columns, keeping remaining commas in text
                    raw_values = line_strip.split(":", 1)[1].lstrip()
                    num_expected = len(format_cols) if format_cols else (text_idx + 1)
                    cols = raw_values.split(",", num_expected - 1)

                    if len(cols) > max(start_idx, end_idx):
                        raw_start = cols[start_idx]
                        raw_end = cols[end_idx]
                        raw_text = cols[text_idx] if len(cols) > text_idx else ""

                        clean_text = cls.clean_dialogue_text(raw_text)
                        if not clean_text:
                            continue

                        srt_start = cls.ass_time_to_srt_time(raw_start)
                        srt_end = cls.ass_time_to_srt_time(raw_end)
                        start_sec = cls.time_str_to_seconds(srt_start)

                        cues.append((start_sec, srt_start, srt_end, clean_text))

        # Sort chronologically
        cues.sort(key=lambda c: c[0])

        # Renumber and format as SRT
        output_blocks = []
        for idx, (_, srt_start, srt_end, text) in enumerate(cues, start=1):
            output_blocks.append(f"{idx}\n{srt_start} --> {srt_end}\n{text}\n")

        return "\n".join(output_blocks).strip() + "\n"

    @classmethod
    def vtt_to_srt(cls, vtt_content: str) -> str:
        """
        Convert WebVTT formatted subtitles into standard SRT.
        """
        # Remove WEBVTT header and cue notes
        vtt_lines = vtt_content.replace("\r\n", "\n").split("\n")
        filtered_lines = []
        skip_header = True

        for line in vtt_lines:
            stripped = line.strip()
            if skip_header:
                if stripped.startswith("WEBVTT") or stripped.startswith("NOTE"):
                    continue
                if not stripped:
                    skip_header = False
                    continue
            filtered_lines.append(line)

        raw_text = "\n".join(filtered_lines)
        # Convert timestamps: 00:01:23.450 -> 00:01:23,450
        time_pattern = re.compile(
            r"(\d{2}:\d{2}:\d{2})\.(\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2})\.(\d{3})"
        )
        converted = time_pattern.sub(r"\1,\2 --> \3,\4", raw_text)

        # Handle 2-part timestamps (01:23.450 -> 00:01:23,450)
        short_time_pattern = re.compile(
            r"(?<!:)(\b\d{2}:\d{2})\.(\d{3})\s+-->\s+(\b\d{2}:\d{2})\.(\d{3})"
        )
        converted = short_time_pattern.sub(r"00:\1,\2 --> 00:\3,\4", converted)

        return cls.sanitize_srt(converted)

    @classmethod
    def sanitize_srt(cls, srt_content: str) -> str:
        """
        Clean existing SRT by normalizing line endings, renumbering, and removing broken blocks.
        """
        normalized = srt_content.replace("\r\n", "\n").replace("\r", "\n")
        # Split into blocks separated by blank lines
        blocks = re.split(r"\n\s*\n", normalized.strip())

        output_cues = []
        timestamp_re = re.compile(
            r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{2,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{2,3})"
        )

        for block in blocks:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if not lines:
                continue

            time_line_idx = -1
            match = None
            for i, line in enumerate(lines):
                m = timestamp_re.search(line)
                if m:
                    time_line_idx = i
                    match = m
                    break

            if not match or time_line_idx == -1:
                continue

            raw_start, raw_end = match.group(1), match.group(2)
            srt_start = cls.ass_time_to_srt_time(raw_start)
            srt_end = cls.ass_time_to_srt_time(raw_end)

            text_lines = lines[time_line_idx + 1:]
            clean_text = cls.clean_dialogue_text("\n".join(text_lines))
            if not clean_text:
                continue

            start_sec = cls.time_str_to_seconds(srt_start)
            output_cues.append((start_sec, srt_start, srt_end, clean_text))

        output_cues.sort(key=lambda c: c[0])

        formatted_blocks = []
        for idx, (_, srt_start, srt_end, text) in enumerate(output_cues, start=1):
            formatted_blocks.append(f"{idx}\n{srt_start} --> {srt_end}\n{text}\n")

        return "\n".join(formatted_blocks).strip() + "\n"
