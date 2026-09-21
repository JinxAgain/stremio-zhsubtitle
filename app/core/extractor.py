"""Archive extraction, charset correction, and target subtitle selection engine."""

import gzip
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")
ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz")

BILINGUAL_REGEX = re.compile(
    r'(?:双语|中英|简英|繁英|英简|英繁|'
    r'(?:chs|cht|zh|chi|zho)[&+._ /-]*(?:eng?|english)|'
    r'(?:eng?|english)[&+._ /-]*(?:chs|cht|zh|chi|zho)|'
    r'\[(?:chs|cht|zh)[^\]]*\].*?\[(?:eng?|english)[^\]]*\]|'
    r'\[(?:eng?|english)[^\]]*\].*?\[(?:chs|cht|zh)[^\]]*\])',
    re.IGNORECASE
)


def fix_archive_filename(raw_name: str) -> str:
    """Recover GBK / GB18030 encoded filenames misdecoded as CP437 by standard zip utilities."""
    try:
        return raw_name.encode("cp437").decode("gb18030")
    except Exception:
        try:
            return raw_name.encode("cp437").decode("gbk")
        except Exception:
            return raw_name


class SubtitleExtractor:
    """Unpacks subtitle archives and selects the best matching subtitle for the target episode."""

    @classmethod
    def extract_best_subtitle(
        cls,
        content_bytes: bytes,
        original_filename: str,
        episode: Optional[int] = None,
        prefer_bilingual: bool = True,
        prefer_traditional: bool = False
    ) -> Tuple[Optional[bytes], str]:
        """
        Extract archive contents and return (subtitle_bytes, subtitle_name) for the requested episode.
        """
        if not content_bytes:
            return None, ""

        lower_name = original_filename.lower()

        # Case 1: GZIP stream
        if content_bytes[:2] == b"\x1f\x8b" or lower_name.endswith((".gz", ".tgz")):
            try:
                decompressed = gzip.decompress(content_bytes)
                clean_name = re.sub(r"\.gz$", "", original_filename, flags=re.IGNORECASE)
                return cls.extract_best_subtitle(
                    decompressed,
                    clean_name,
                    episode=episode,
                    prefer_bilingual=prefer_bilingual,
                    prefer_traditional=prefer_traditional
                )
            except Exception as e:
                logger.warning(f"Gzip extraction failed: {e}")

        # Case 2: Standalone subtitle file
        ext = os.path.splitext(lower_name)[1]
        if ext in SUBTITLE_EXTENSIONS:
            return content_bytes, original_filename

        # Case 3: ZIP Archive (pure in-memory extraction)
        if content_bytes[:2] == b"PK" or lower_name.endswith(".zip"):
            res = cls._extract_from_zip(
                content_bytes,
                episode=episode,
                prefer_bilingual=prefer_bilingual,
                prefer_traditional=prefer_traditional
            )
            if res[0] is not None:
                return res

        # Case 4: Generic Archive (.rar, .7z, .tar, etc.) using disk temporary unpacker
        return cls._extract_generic_archive(
            content_bytes,
            original_filename,
            episode=episode,
            prefer_bilingual=prefer_bilingual,
            prefer_traditional=prefer_traditional
        )

    @classmethod
    def _extract_from_zip(
        cls,
        content_bytes: bytes,
        episode: Optional[int],
        prefer_bilingual: bool,
        prefer_traditional: bool
    ) -> Tuple[Optional[bytes], str]:
        """Extract subtitles from ZIP archive in-memory."""
        try:
            with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
                files_map: Dict[str, bytes] = {}
                for info in zf.infolist():
                    if info.is_dir():
                        continue

                    # Recover filename encoding if UTF-8 bit (0x800) is absent
                    if info.flag_bits & 0x800:
                        name = info.filename
                    else:
                        name = fix_archive_filename(info.filename)

                    base_name = os.path.basename(name)
                    if base_name.lower().endswith(SUBTITLE_EXTENSIONS):
                        files_map[base_name] = zf.read(info)

                if not files_map:
                    return None, ""

                best_name = cls.pick_best_file(
                    list(files_map.keys()),
                    episode=episode,
                    prefer_bilingual=prefer_bilingual,
                    prefer_traditional=prefer_traditional
                )
                if best_name and best_name in files_map:
                    return files_map[best_name], best_name
        except Exception as e:
            logger.warning(f"ZIP unpack error: {e}")

        return None, ""

    @classmethod
    def _extract_generic_archive(
        cls,
        content_bytes: bytes,
        original_filename: str,
        episode: Optional[int],
        prefer_bilingual: bool,
        prefer_traditional: bool
    ) -> Tuple[Optional[bytes], str]:
        """Unpack RAR / 7Z / TAR files using available system and python unpackers."""
        temp_dir = tempfile.mkdtemp(prefix="zhsub_extract_")
        archive_path = os.path.join(temp_dir, original_filename or "archive.bin")

        try:
            with open(archive_path, "wb") as f:
                f.write(content_bytes)

            extract_dest = os.path.join(temp_dir, "out")
            os.makedirs(extract_dest, exist_ok=True)

            unpacked = False

            # 1. Try 7-Zip CLI if present
            exe_7z = shutil.which("7z") or shutil.which("7za")
            if not exe_7z:
                for candidate in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
                    if os.path.isfile(candidate):
                        exe_7z = candidate
                        break
            if exe_7z:
                try:
                    res = subprocess.run([exe_7z, "x", "-y", f"-o{extract_dest}", archive_path],
                                         capture_output=True, timeout=30)
                    if res.returncode == 0:
                        unpacked = True
                except Exception:
                    pass

            # 2. Try py7zr
            if not unpacked and original_filename.lower().endswith(".7z"):
                try:
                    import py7zr
                    with py7zr.SevenZipFile(archive_path, mode="r") as z:
                        z.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 3. Try rarfile
            if not unpacked and original_filename.lower().endswith(".rar"):
                try:
                    import rarfile
                    with rarfile.RarFile(archive_path) as rf:
                        rf.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 4. Try tarfile
            if not unpacked:
                try:
                    import tarfile
                    with tarfile.open(archive_path, "r:*") as tf:
                        tf.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 5. Fallback to shutil.unpack_archive
            if not unpacked:
                try:
                    shutil.unpack_archive(archive_path, extract_dest)
                    unpacked = True
                except Exception:
                    pass

            if unpacked:
                found_files: Dict[str, str] = {}
                for root, _, files in os.walk(extract_dest):
                    for f in files:
                        if f.lower().endswith(SUBTITLE_EXTENSIONS):
                            found_files[f] = os.path.join(root, f)

                if found_files:
                    best_name = cls.pick_best_file(
                        list(found_files.keys()),
                        episode=episode,
                        prefer_bilingual=prefer_bilingual,
                        prefer_traditional=prefer_traditional
                    )
                    if best_name and best_name in found_files:
                        with open(found_files[best_name], "rb") as sub_f:
                            return sub_f.read(), best_name

        except Exception as e:
            logger.error(f"Generic archive extraction failed: {e}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return None, ""

    @classmethod
    def pick_best_file(
        cls,
        filenames: List[str],
        episode: Optional[int] = None,
        prefer_bilingual: bool = True,
        prefer_traditional: bool = False
    ) -> Optional[str]:
        """
        Rank subtitle files based on episode number, language preference, and format.
        """
        if not filenames:
            return None

        chs_pat = re.compile(r"chs|gb|sc|简|简体|简中|zh-cn|zh-hans|chi|zho", re.IGNORECASE)
        cht_pat = re.compile(r"cht|tc|big5|繁|繁体|繁體|繁中|zh-tw|zh-hk|zh-hant", re.IGNORECASE)

        def score_file(filename: str) -> Tuple[int, int, int]:
            lower = filename.lower()

            # 1. Target episode match score
            ep_score = 0
            if episode is not None:
                ep_patterns = [
                    rf"\b[eE][pP]?0*{episode}\b",
                    rf"s\d{{1,2}}e0*{episode}\b",
                    rf"第\s*0*{episode}\s*[集话話]",
                    rf"\[0*{episode}\]",
                    rf"\b0*{episode}\b"
                ]
                for pat in ep_patterns:
                    if re.search(pat, lower):
                        ep_score = 1000
                        break

            # 2. Language match score
            lang_score = 0
            is_bilingual = bool(BILINGUAL_REGEX.search(lower))
            is_cht = bool(cht_pat.search(lower))
            is_chs = bool(chs_pat.search(lower))

            if prefer_bilingual and is_bilingual:
                lang_score = 500
            elif prefer_traditional and is_cht:
                lang_score = 450
            elif not prefer_traditional and is_chs:
                lang_score = 400
            elif is_bilingual:
                lang_score = 350
            elif is_chs or is_cht:
                lang_score = 300

            # 3. Format preference score (.ass/.ssa > .srt > .vtt)
            fmt_score = 0
            if lower.endswith((".ass", ".ssa")):
                fmt_score = 30
            elif lower.endswith(".srt"):
                fmt_score = 20
            elif lower.endswith(".vtt"):
                fmt_score = 10

            return (ep_score, lang_score, fmt_score)

        sorted_files = sorted(filenames, key=score_file, reverse=True)
        return sorted_files[0]
