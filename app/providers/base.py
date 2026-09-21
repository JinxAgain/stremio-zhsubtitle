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


def to_cn_season(season: Optional[int]) -> str:
    """Convert integer season number to Chinese text representation (e.g. 2 -> '第二季')."""
    if season is None:
        return ""
    cn_num = CN_NUM_MAP.get(season, str(season))
    return f"第{cn_num}季"


def is_episode_match(title: str, season: Optional[int], episode: Optional[int]) -> Tuple[bool, bool]:
    """
    Check if a subtitle title matches the requested season and episode.
    Returns (is_match, is_collection).
    """
    if season is None and episode is None:
        return True, False

    title_lower = title.lower()

    # 1. Exact episode match (e.g. S01E14, E14, EP14, 第14集, [14])
    if episode is not None:
        ep_patterns = [
            rf"\b[eE][pP]?0*{episode}\b",
            rf"s\d{{1,2}}e0*{episode}\b",
            rf"第\s*0*{episode}\s*[集话話]",
            rf"\[0*{episode}\]"
        ]
        if any(re.search(pat, title_lower) for pat in ep_patterns):
            return True, False

    # 2. Season pack / collection match
    pack_regex = re.compile(r"全|合集|pack|complete|\b\d+-\d+\b|全部|整季|季全", re.IGNORECASE)
    is_pack = bool(pack_regex.search(title_lower))

    if season is not None:
        cn_s = to_cn_season(season).lower()
        s_tokens = [f"s{season:02d}", f"s{season}", f"season {season}", cn_s, f"第{season}季"]

        # Check if explicitly belongs to a different season
        for s_idx in range(1, 20):
            if s_idx != season:
                cn_other = to_cn_season(s_idx).lower()
                other_tokens = [f"s{s_idx:02d}", f"season {s_idx}", cn_other, f"第{s_idx}季"]
                if any(tok in title_lower for tok in other_tokens):
                    return False, False

        matches_target_season = any(tok in title_lower for tok in s_tokens)
        if matches_target_season and (is_pack or not re.search(r"\b[eE][pP]?\d+\b|第\s*\d+\s*[集话話]", title_lower)):
            return True, True

    if is_pack:
        return True, True

    return False, False


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
