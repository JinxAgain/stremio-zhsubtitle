"""Base provider interface and data structures."""

import logging
import re
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CN_NUM_MAP = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
    11: "十一", 12: "十二", 13: "十三", 14: "十四", 15: "十五",
    16: "十六", 17: "十七", 18: "十八", 19: "十九", 20: "二十"
}

CN_SEASON_REV = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}


def to_cn_season(season: Optional[int]) -> str:
    """Convert integer season number to Chinese text representation (e.g. 2 -> '第二季')."""
    if season is None:
        return ""
    cn_num = CN_NUM_MAP.get(season, str(season))
    return f"第{cn_num}季"


def extract_seasons_from_text(text: str) -> set[int]:
    """Extract all explicit season numbers referenced in a title string."""
    seasons = set()
    if not text:
        return seasons

    # S01, S1, S04
    for m in re.finditer(r"\b[sS](\d{1,2})(?:[^\d]|$)", text):
        seasons.add(int(m.group(1)))

    # Season 1, Season 04
    for m in re.finditer(r"\bseason\s*(\d{1,2})\b", text, flags=re.IGNORECASE):
        seasons.add(int(m.group(1)))

    # 第1季, 第04季
    for m in re.finditer(r"第\s*(\d{1,2})\s*季", text):
        seasons.add(int(m.group(1)))

    # 第一季, 第四季, etc.
    for m in re.finditer(r"第\s*([一二三四五六七八九十]+)\s*季", text):
        val = CN_SEASON_REV.get(m.group(1))
        if val:
            seasons.add(val)

    # Season ranges: S01-S03, S1-S4
    for m in re.finditer(r"\b[sS](\d{1,2})\s*[-~至到]\s*[sS]?(\d{1,2})\b", text):
        s_start, s_end = int(m.group(1)), int(m.group(2))
        if 0 < s_start <= s_end <= 30:
            seasons.update(range(s_start, s_end + 1))

    # Chinese season ranges: 第1-3季, 第一季至第三季
    for m in re.finditer(r"第\s*(\d{1,2})\s*[-~至到]\s*(\d{1,2})\s*季", text):
        s_start, s_end = int(m.group(1)), int(m.group(2))
        if 0 < s_start <= s_end <= 30:
            seasons.update(range(s_start, s_end + 1))

    return seasons


def is_episode_match(title: str, season: Optional[int], episode: Optional[int]) -> Tuple[bool, bool]:
    """
    Check if a subtitle title matches the requested season and episode.
    Returns (is_match, is_collection).
    """
    if season is None and episode is None:
        return True, False

    title_lower = title.lower()

    # Step 1: Strict Season Conflict Check
    if season is not None:
        found_seasons = extract_seasons_from_text(title)
        if found_seasons and season not in found_seasons:
            # Title explicitly belongs to a different season (e.g. S01 when looking for S04)
            return False, False

    # Step 2: Target Episode Match
    if episode is not None:
        ep_patterns = [
            rf"\b[eE][pP]?0*{episode}\b",
            rf"第\s*0*{episode}\s*[集话話]",
            rf"\[0*{episode}\]",
            rf"(?:^|[._ -])0*{episode}(?:[._ -]|\.srt|\.ass|\.ssa|\.vtt)"
        ]
        if season is not None:
            ep_patterns.insert(0, rf"[sS]0*{season}[eE]0*{episode}\b")

        matched_ep = any(re.search(pat, title, flags=re.IGNORECASE) for pat in ep_patterns)

        # Check if title explicitly specifies a different single episode (and not target episode)
        has_different_ep = False
        all_eps = [
            int(num) for num in re.findall(r"\b[eE][pP]?(\d{1,3})\b", title_lower)
        ]
        if all_eps and episode not in all_eps:
            has_different_ep = True
        else:
            cn_eps = [
                int(num) for num in re.findall(r"第\s*(\d{1,3})\s*[集话話]", title_lower)
            ]
            if cn_eps and episode not in cn_eps:
                has_different_ep = True

        if matched_ep:
            return True, False
        if has_different_ep:
            return False, False

    # Step 3: Season pack / collection match
    pack_regex = re.compile(r"全|合集|pack|complete|\b\d+-\d+\b|全部|整季|季全|全集", re.IGNORECASE)
    is_pack = bool(pack_regex.search(title_lower))

    if season is not None:
        found_seasons = extract_seasons_from_text(title)
        matches_target_season = (season in found_seasons) if found_seasons else False
        has_explicit_ep = bool(re.search(r"(?:[eE][pP]?|第\s*)\d+|(?:\b|\D)\d{1,2}\s*[集话話]", title_lower))

        if matches_target_season and (is_pack or not has_explicit_ep):
            return True, True

    if is_pack and season is None:
        return True, True

    return False, False


def extract_meta_from_filename(filename: str) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
    """Extract (title, year, season, episode) from release filename."""
    if not filename:
        return "", None, None, None

    clean = urllib.parse.unquote(filename)
    clean = re.sub(r"\.(mkv|mp4|avi|ts|mov|m4v|iso|wmv|flv)$", "", clean, flags=re.IGNORECASE)

    # Season and Episode (e.g. S01E14)
    s_match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", clean)
    season = int(s_match.group(1)) if s_match else None
    episode = int(s_match.group(2)) if s_match else None

    # Year (19xx or 20xx)
    y_match = re.search(r"\b(19\d\d|20\d\d)\b", clean)
    year = int(y_match.group(1)) if y_match else None

    # Cut off title before season, year, or resolution markers
    cutoff_patterns = [
        r"[Ss]\d{1,2}[Ee]\d{1,2}",
        r"\b(19\d\d|20\d\d)\b",
        r"\b(1080p|720p|2160p|4k|bluray|web-dl|webrip|hdtv|remux)\b",
    ]
    min_idx = len(clean)
    for pat in cutoff_patterns:
        m = re.search(pat, clean, flags=re.IGNORECASE)
        if m and m.start() < min_idx and m.start() > 0:
            min_idx = m.start()

    title_part = clean[:min_idx].strip(" .-_")
    title = re.sub(r"[._]", " ", title_part).strip()

    return title, year, season, episode


@dataclass
class VideoQueryMeta:
    """Metadata describing the target movie or series episode."""
    imdb_id: str
    media_type: str  # "movie" or "series"
    season: Optional[int] = None
    episode: Optional[int] = None
    title: str = ""
    year: Optional[int] = None
    filename: str = ""

    @property
    def is_tv(self) -> bool:
        return self.media_type == "series" or self.season is not None


@dataclass
class SubtitleTags:
    """Attributes and classification tags parsed for a subtitle item."""
    provider: str = ""
    lang: List[str] = field(default_factory=list)
    bilingual: bool = False
    fmt: List[str] = field(default_factory=list)
    source: List[str] = field(default_factory=list)
    fansub: str = ""
    uploader: str = ""
    collection: bool = False
    imdb_matched: bool = False


@dataclass
class SubtitleCandidate:
    """Candidate subtitle item returned from a provider search."""
    id: str
    provider: str
    title: str
    page_url: str
    tags: SubtitleTags = field(default_factory=SubtitleTags)
    rate: float = 0.0
    downloads_count: int = 0
    upload_date: str = ""
    score: float = 0.0


class BaseProvider(ABC):
    """Abstract base class for subtitle providers."""

    name: str = "base"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    @abstractmethod
    def search(self, meta: VideoQueryMeta) -> List[SubtitleCandidate]:
        """Search subtitles for the given video metadata."""
        pass

    @abstractmethod
    def download(self, candidate: SubtitleCandidate) -> Tuple[Optional[bytes], str]:
        """Download subtitle archive or file content, returning (content_bytes, filename)."""
        pass

    def extract_filename_from_response(self, response: requests.Response, default: str = "subtitle.bin") -> str:
        """Extract filename from HTTP Content-Disposition header or URL path."""
        cd = response.headers.get("Content-Disposition", "")
        if cd:
            # Handle filename*=utf-8''encoded_name
            match_star = re.search(r"filename\*\s*=\s*(?:utf-8|UTF-8)''([^;]+)", cd)
            if match_star:
                try:
                    return urllib.parse.unquote(match_star.group(1).strip('"\' '))
                except Exception:
                    pass

            # Handle standard filename="name"
            match_fn = re.search(r'filename\s*=\s*"([^"]+)"', cd) or re.search(r"filename\s*=\s*([^;]+)", cd)
            if match_fn:
                name = match_fn.group(1).strip('"\' ')
                try:
                    # Attempt latin1 -> gbk or utf-8 recovery if headers were mishandled
                    return name.encode("latin1").decode("utf-8")
                except Exception:
                    try:
                        return name.encode("latin1").decode("gbk")
                    except Exception:
                        return name

        # Fallback to URL path
        url_path = urllib.parse.urlparse(response.url or "").path
        base = url_path.split("/")[-1].split("?")[0].strip()
        if base and "." in base:
            return urllib.parse.unquote(base)

        return default
