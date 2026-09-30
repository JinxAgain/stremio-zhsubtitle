"""Archive extraction, charset correction, and target subtitle selection engine."""

import gzip
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
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
    r'\[(?:eng?|english|英语)[^\]]*\].*?\[(?:chs|cht|zh|简|繁|简体|繁体)[^\]]*\])',
    re.IGNORECASE
)


def bootstrap_linux_7zz() -> Optional[str]:
    """Auto-bootstrap official standalone static 7zzs for Linux x86_64 to data/bin/7zz."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return None

    target_dir = os.path.join("data", "bin")
    target_bin = os.path.join(target_dir, "7zz")
    if os.path.isfile(target_bin) and os.access(target_bin, os.X_OK):
        return target_bin

    try:
        os.makedirs(target_dir, exist_ok=True)
        url = "https://www.7-zip.org/a/7z2301-linux-x64.tar.xz"
        logger.info(f"[Extractor] Downloading official static 7zz from {url} for RAR5 support...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            member = tf.getmember("7zzs")
            extracted = tf.extractfile(member)
            if extracted:
                with open(target_bin, "wb") as out_f:
                    out_f.write(extracted.read())
                os.chmod(target_bin, 0o755)
                logger.info(f"[Extractor] Successfully installed official static 7zz to '{target_bin}'")
                return target_bin
    except Exception as e:
        logger.warning(f"[Extractor] Failed to bootstrap static 7zz on Linux: {e}")

    return None


def get_7z_binary() -> Optional[str]:
    """Find the best available 7z binary, prioritizing official 7zz with full RAR5 support."""
    # 1. Prefer official 7zz/7zzs command in PATH
    exe = shutil.which("7zz") or shutil.which("7zzs")
    if exe:
        return exe

    # 2. Check candidate paths for official 7zz
    candidates = (
        "/usr/local/bin/7zz",
        "/usr/local/bin/7zzs",
        "/usr/bin/7zz",
        os.path.join("data", "bin", "7zz"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe"
    )
    for c in candidates:
        if os.path.isfile(c):
            return c

    # 3. Fallback to generic 7z / 7za (e.g. Debian p7zip)
    exe = shutil.which("7z") or shutil.which("7za")
    if exe:
        return exe
    if os.path.isfile("/usr/bin/7z"):
        return "/usr/bin/7z"

    return None


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
        if lower_name.endswith(SUBTITLE_EXTENSIONS):
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

            # 1. Try 7-Zip CLI (prefer official 7zz with native RAR5 support)
            exe_7z = get_7z_binary()
            if exe_7z:
                try:
                    res = subprocess.run(
                        [exe_7z, "x", "-y", f"-o{extract_dest}", archive_path],
                        capture_output=True,
                        timeout=30
                    )
                    if res.returncode == 0:
                        unpacked = True
                    else:
                        out_msg = (res.stderr or res.stdout).decode("utf-8", errors="ignore").strip()
                        logger.warning(
                            f"[Extractor] 7-Zip ({exe_7z}) failed with code {res.returncode}: {out_msg[:300]}"
                        )
                        # If p7zip lacks RAR5 algorithm ("Unsupported Method"), fallback to official static 7zz
                        if "Unsupported Method" in out_msg and exe_7z != os.path.join("data", "bin", "7zz"):
                            static_7zz = bootstrap_linux_7zz()
                            if static_7zz:
                                res2 = subprocess.run(
                                    [static_7zz, "x", "-y", f"-o{extract_dest}", archive_path],
                                    capture_output=True,
                                    timeout=30
                                )
                                if res2.returncode == 0:
                                    unpacked = True
                                else:
                                    logger.warning(f"[Extractor] Bootstrapped static 7zz failed with code {res2.returncode}")
                except Exception as e:
                    logger.warning(f"[Extractor] 7-Zip extraction failed: {e}")

            # 2. Try native unrar CLI for RAR files if available
            if not unpacked and original_filename.lower().endswith(".rar"):
                exe_unrar = shutil.which("unrar") or (
                    candidate if os.path.isfile(candidate := os.path.join("data", "bin", "unrar")) else None
                )
                if exe_unrar:
                    try:
                        res = subprocess.run(
                            [exe_unrar, "x", "-y", "-o+", archive_path, extract_dest + os.sep],
                            capture_output=True,
                            timeout=30
                        )
                        if res.returncode == 0:
                            unpacked = True
                        else:
                            out_msg = (res.stderr or res.stdout).decode("utf-8", errors="ignore").strip()
                            logger.warning(
                                f"[Extractor] unrar failed with code {res.returncode}: {out_msg[:300]}"
                            )
                    except Exception as e:
                        logger.warning(f"[Extractor] unrar extraction failed: {e}")

            # 3. Try unar CLI
            if not unpacked:
                exe_unar = shutil.which("unar") or (
                    candidate if os.path.isfile(candidate := os.path.join("data", "bin", "unar")) else None
                )
                if exe_unar:
                    try:
                        res = subprocess.run(
                            [exe_unar, "-o", extract_dest, "-D", "-f", archive_path],
                            capture_output=True,
                            timeout=30
                        )
                        if res.returncode == 0:
                            unpacked = True
                        else:
                            out_msg = (res.stderr or res.stdout).decode("utf-8", errors="ignore").strip()
                            logger.warning(
                                f"[Extractor] unar failed with code {res.returncode}: {out_msg[:300]}"
                            )
                    except Exception as e:
                        logger.warning(f"[Extractor] unar extraction failed: {e}")

            # 4. If RAR archive still not unpacked on Linux x86_64, try bootstrapping static 7zz as fallback
            if not unpacked and original_filename.lower().endswith(".rar"):
                static_7zz = bootstrap_linux_7zz()
                if static_7zz:
                    try:
                        res = subprocess.run(
                            [static_7zz, "x", "-y", f"-o{extract_dest}", archive_path],
                            capture_output=True,
                            timeout=30
                        )
                        if res.returncode == 0:
                            unpacked = True
                        else:
                            logger.warning(f"[Extractor] Fallback static 7zz failed with code {res.returncode}")
                    except Exception as e:
                        logger.warning(f"[Extractor] Fallback static 7zz extraction error: {e}")

            # 4. Try py7zr
            if not unpacked and original_filename.lower().endswith(".7z"):
                try:
                    import py7zr
                    with py7zr.SevenZipFile(archive_path, mode="r") as z:
                        z.extractall(extract_dest)
                    unpacked = True
                except Exception as e:
                    logger.debug(f"[Extractor] py7zr extraction failed: {e}")

            # 5. Try rarfile module
            if not unpacked and original_filename.lower().endswith(".rar"):
                try:
                    import rarfile
                    if exe_unar:
                        rarfile.UNRAR_TOOL = exe_unar
                    elif exe_7z:
                        rarfile.UNRAR_TOOL = exe_7z
                    with rarfile.RarFile(archive_path) as rf:
                        rf.extractall(extract_dest)
                    unpacked = True
                except Exception as e:
                    logger.debug(f"[Extractor] rarfile extraction failed: {e}")

            # 6. Try tarfile
            if not unpacked:
                try:
                    import tarfile
                    with tarfile.open(archive_path, "r:*") as tf:
                        tf.extractall(extract_dest)
                    unpacked = True
                except Exception:
                    pass

            # 7. Fallback to shutil.unpack_archive
            if not unpacked:
                try:
                    shutil.unpack_archive(archive_path, extract_dest)
                    unpacked = True
                except Exception:
                    pass

            if not unpacked:
                available_tools = []
                if exe_unar:
                    available_tools.append(f"unar ({exe_unar})")
                if exe_7z:
                    available_tools.append(f"7z ({exe_7z})")
                if shutil.which("unrar"):
                    available_tools.append(f"unrar ({shutil.which('unrar')})")
                logger.error(
                    f"[Extractor] Failed to unpack generic archive '{original_filename}'. "
                    f"Available tools: {available_tools or 'None'}. "
                    f"For RAR archives, install 'unar' or 'p7zip-full' (e.g. apt-get install -y unar p7zip-full) "
                    f"or deploy using Docker."
                )

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

