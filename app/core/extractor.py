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
    r'(?:双语|中英|简英|繁英|英简|英繁|zh[-_]en|'
    r'(?:chs|cht|zh|chi|zho|简|繁|简体|繁体)[&+._ /-]*(?:eng?|english|英文|英语)|'
    r'(?:eng?|english|英文|英语)[&+._ /-]*(?:chs|cht|zh|chi|zho|简|繁|简体|繁体)|'
    r'\[(?:chs|cht|zh|简|繁|简体|繁体)[^\]]*\].*?\[(?:eng?|english|英文|英语)[^\]]*\]|'
    r'\[(?:eng?|english|英文|英语)[^\]]*\].*?\[(?:chs|cht|zh|简|繁|简体|繁体)[^\]]*\])',
    re.IGNORECASE
)


def is_direct_subtitle(content_bytes: bytes, filename: str = "") -> bool:
    """
    Determine if content is directly a subtitle file (SRT, ASS, SSA, VTT),
    even if misnamed or without extension.
    """
    if not content_bytes or len(content_bytes) < 10:
        return False

    # 1. Reject known binary archive magic bytes
    if content_bytes[:4] in (b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf") or content_bytes[:2] == b"\x1f\x8b":
        return False
    if len(content_bytes) > 262 and content_bytes[257:262] == b"ustar":
        return False

    # 2. Check standard subtitle file extensions
    ext = os.path.splitext(filename.lower())[1]
    if ext in SUBTITLE_EXTENSIONS:
        return True

    # 3. Content-based signature inspection (detect SRT/VTT/ASS text cues)
    sample = content_bytes[:4096]
    if b"-->" in sample:
        return True
    if b"[Script Info]" in sample or b"Dialogue:" in sample or b"Format:" in sample:
        return True
    if b"WEBVTT" in sample:
        return True

    return False


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

        # Case 2: Standalone subtitle file (by extension or content inspection)
        if is_direct_subtitle(content_bytes, original_filename):
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
                    # Check for nested archives inside zip (e.g. season pack with per-episode zips)
                    nested_map: Dict[str, bytes] = {}
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        base_name = os.path.basename(info.filename)
                        if base_name.lower().endswith(ARCHIVE_EXTENSIONS):
                            nested_map[base_name] = zf.read(info)
                    if nested_map:
                        best_archive = cls.pick_best_file(
                            list(nested_map.keys()),
                            episode=episode,
                            prefer_bilingual=prefer_bilingual,
                            prefer_traditional=prefer_traditional
                        )
                        if best_archive and best_archive in nested_map:
                            return cls.extract_best_subtitle(
                                nested_map[best_archive],
                                best_archive,
                                episode=episode,
                                prefer_bilingual=prefer_bilingual,
                                prefer_traditional=prefer_traditional
                            )
                    return None, ""

                logger.info(f"[Extractor] Found {len(files_map)} subtitle files in ZIP archive: {list(files_map.keys())[:10]}")
                best_name = cls.pick_best_file(
                    list(files_map.keys()),
                    episode=episode,
                    prefer_bilingual=prefer_bilingual,
                    prefer_traditional=prefer_traditional
                )
                if best_name and best_name in files_map:
                    logger.info(f"[Extractor] Selected best file '{best_name}' (target episode: {episode})")
                    return files_map[best_name], best_name
                else:
                    logger.warning(f"[Extractor] No suitable file picked from ZIP (target episode: {episode})")
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

            # 1. Try unar CLI (supports all RAR / RAR5 / 7Z / ZIP formats on Linux without non-free dependencies)
            exe_unar = shutil.which("unar")
            if exe_unar:
                try:
                    res = subprocess.run(
                        [exe_unar, "-o", extract_dest, "-D", "-f", archive_path],
                        capture_output=True,
                        timeout=30
                    )
                    if res.returncode == 0:
                        unpacked = True
                except Exception as e:
                    logger.warning(f"unar extraction failed: {e}")

            # 2. Try 7-Zip CLI if present
            if not unpacked:
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

            # 3. Try py7zr
            if not unpacked and original_filename.lower().endswith(".7z"):
                try:
                    import py7zr
                    with py7zr.SevenZipFile(archive_path, mode="r") as z:
                        z.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 4. Try rarfile
            if not unpacked and original_filename.lower().endswith(".rar"):
                try:
                    import rarfile
                    if exe_unar:
                        rarfile.UNRAR_TOOL = exe_unar
                    with rarfile.RarFile(archive_path) as rf:
                        rf.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 5. Try tarfile
            if not unpacked:
                try:
                    import tarfile
                    with tarfile.open(archive_path, "r:*") as tf:
                        tf.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 6. Fallback to shutil.unpack_archive
            if not unpacked:
                try:
                    shutil.unpack_archive(archive_path, extract_dest)
                    unpacked = True
                except Exception:
                    pass

            if unpacked:
                found_files: Dict[str, str] = {}
                for root, _, files in os.walk(extract_dest):
                    if "__MACOSX" in root:
                        continue
                    for f in files:
                        if f.startswith("._"):
                            continue
                        if f.lower().endswith(SUBTITLE_EXTENSIONS):
                            found_files[f] = os.path.join(root, f)

                if not found_files:
                    # Check for nested archives inside archive (e.g. RAR containing per-episode ZIPs)
                    nested_archives: Dict[str, str] = {}
                    for root, _, files in os.walk(extract_dest):
                        if "__MACOSX" in root:
                            continue
                        for f in files:
                            if f.startswith("._"):
                                continue
                            if f.lower().endswith(ARCHIVE_EXTENSIONS):
                                nested_archives[f] = os.path.join(root, f)
                    if nested_archives:
                        best_archive = cls.pick_best_file(
                            list(nested_archives.keys()),
                            episode=episode,
                            prefer_bilingual=prefer_bilingual,
                            prefer_traditional=prefer_traditional
                        )
                        if best_archive and best_archive in nested_archives:
                            with open(nested_archives[best_archive], "rb") as nested_f:
                                nested_bytes = nested_f.read()
                            return cls.extract_best_subtitle(
                                nested_bytes,
                                best_archive,
                                episode=episode,
                                prefer_bilingual=prefer_bilingual,
                                prefer_traditional=prefer_traditional
                            )

                if found_files:
                    logger.info(f"[Extractor] Unpacked {len(found_files)} subtitle files from '{original_filename}': {list(found_files.keys())[:10]}")
                    best_name = cls.pick_best_file(
                        list(found_files.keys()),
                        episode=episode,
                        prefer_bilingual=prefer_bilingual,
                        prefer_traditional=prefer_traditional
                    )
                    if best_name and best_name in found_files:
                        logger.info(f"[Extractor] Selected best file '{best_name}' (target episode: {episode})")
                        with open(found_files[best_name], "rb") as sub_f:
                            return sub_f.read(), best_name
                    else:
                        logger.warning(f"[Extractor] No suitable file picked from '{original_filename}' (target episode: {episode})")
                else:
                    logger.warning(f"[Extractor] No subtitle files (.srt/.ass) found in '{original_filename}' after unpacking")


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
        Handles full season packs and multi-variant single-episode archives.
        """
        if not filenames:
            return None

        chs_pat = re.compile(r"chs|gb|sc|简|简体|简中|zh-cn|zh-hans|chi|zho", re.IGNORECASE)
        cht_pat = re.compile(r"cht|tc|big5|繁|繁体|繁體|繁中|zh-tw|zh-hk|zh-hant", re.IGNORECASE)
        eng_only_pat = re.compile(r"(?:^|[._ -])(?:eng?|english|英文)[._ -]*(?:srt|ass|ssa|vtt)$", re.IGNORECASE)

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
                    rf"(?:^|[._ -])0*{episode}(?:[._ -]|\.srt|\.ass|\.ssa|\.vtt)"
                ]
                matched_target_ep = any(re.search(pat, lower) for pat in ep_patterns)

                # Check if matches a different episode (e.g. S01E01 when looking for S01E04)
                all_eps = [
                    int(num) for num in re.findall(r"\b[eE][pP]?(\d{1,3})\b", lower)
                ]
                has_different_ep = False
                if all_eps:
                    if episode not in all_eps:
                        has_different_ep = True
                else:
                    cn_eps = [
                        int(num) for num in re.findall(r"第\s*(\d{1,3})\s*[集话話]", lower)
                    ]
                    if cn_eps and episode not in cn_eps:
                        has_different_ep = True

                if matched_target_ep:
                    ep_score = 2000
                elif has_different_ep:
                    ep_score = -5000  # Disqualify wrong episode in season pack
                else:
                    ep_score = 500

            # 2. Language match score
            stem, _ = os.path.splitext(filename)
            parts = re.split(r"[._ -]+", stem)
            last_tag = parts[-1].strip().lower() if parts else ""

            # Check if filename ends with English-only indicator (e.g. .英文.srt, .eng.srt)
            is_eng_only_tag = bool(eng_only_pat.match(last_tag))

            is_bilingual = bool(BILINGUAL_REGEX.search(last_tag)) or bool(BILINGUAL_REGEX.search(lower))
            is_cht = bool(cht_pat.search(last_tag)) or bool(cht_pat.search(lower))
            is_chs = bool(chs_pat.search(last_tag)) or bool(chs_pat.search(lower))

            if is_eng_only_tag:
                lang_score = -2000  # Never choose pure English file when Chinese is requested
            elif prefer_bilingual and is_bilingual:
                # Prefer files that explicitly specify bilingual in their specific variant tag
                lang_score = 1100 if BILINGUAL_REGEX.search(last_tag) else 1000
            elif prefer_traditional and is_cht:
                lang_score = 900
            elif not prefer_traditional and is_chs:
                lang_score = 800
            elif is_bilingual:
                lang_score = 700
            elif is_chs:
                lang_score = 600
            elif is_cht:
                lang_score = 500
            else:
                lang_score = 400


            # 3. Format preference score: .srt > .ass > .ssa > .vtt
            fmt_score = 0
            if lower.endswith(".srt"):
                fmt_score = 50
            elif lower.endswith((".ass", ".ssa")):
                fmt_score = 40
            elif lower.endswith(".vtt"):
                fmt_score = 20

            return (ep_score, lang_score, fmt_score)

        sorted_files = sorted(filenames, key=score_file, reverse=True)
        return sorted_files[0]

