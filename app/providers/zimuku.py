"""Zimuku (srtku.com / zmk.pw / zimuku.org) subtitle provider implementation."""

import base64
import logging
import re
import struct
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .base import (
    BaseProvider,
    SubtitleCandidate,
    SubtitleTags,
    VideoQueryMeta,
    to_cn_season,
    is_episode_match,
)
from ..config import settings

logger = logging.getLogger(__name__)

FILE_MIN_SIZE = 100


class ZimukuBmpSolver:
    """Recognizes 5 digits from Zimuku's 100x27 BMP verification image using template matching."""

    IMG_WIDTH, IMG_HEIGHT = 100, 27
    CHAR_WIDTH, NUM_CHARS = 20, 5
    PIXEL_DATA_OFFSET = 54

    SAMPLE_POINTS = [
        (10, 7), (7, 8), (12, 8), (10, 13),
        (7, 19), (12, 19), (10, 20), (6, 13), (14, 13)
    ]

    TEMPLATES = {
        '0': [1, 1, 1, 1, 1, 1, 1, 1, 0],
        '1': [0, 1, 0, 0, 0, 0, 1, 0, 0],
        '2': [1, 0, 1, 0, 1, 0, 1, 0, 0],
        '3': [1, 0, 1, 1, 0, 1, 1, 0, 0],
        '4': [0, 0, 1, 0, 0, 1, 0, 0, 0],
        '5': [1, 1, 0, 0, 0, 1, 1, 0, 0],
        '6': [1, 0, 1, 1, 1, 1, 1, 1, 0],
        '7': [1, 0, 1, 0, 0, 0, 0, 0, 0],
        '8': [1, 1, 1, 1, 1, 1, 1, 0, 0],
        '9': [1, 1, 1, 0, 1, 0, 1, 0, 0],
    }

    def __init__(self, b64_string: str):
        self._data = base64.b64decode(b64_string)
        if len(self._data) < self.PIXEL_DATA_OFFSET or self._data[:2] != b'BM':
            raise ValueError("Invalid BMP data")
        self._stride = (self.IMG_WIDTH * 3 + 3) & ~3

    def recognize(self) -> str:
        result = []
        one_offset = 0
        for i in range(self.NUM_CHARS):
            char_x = i * self.CHAR_WIDTH
            features = [
                1 if self._is_foreground(char_x + px - one_offset, py) else 0
                for px, py in self.SAMPLE_POINTS
            ]
            digit = self._match_digit(features)
            if digit == '1':
                one_offset += 1
            elif digit == '4':
                one_offset -= 1
            result.append(digit)
        return "".join(result)

    def _is_foreground(self, x: int, y: int, threshold: int = 70) -> bool:
        bmp_y = self.IMG_HEIGHT - 1 - y
        offset = self.PIXEL_DATA_OFFSET + bmp_y * self._stride + x * 3
        if offset + 2 >= len(self._data):
            return False
        b, g, r = self._data[offset], self._data[offset + 1], self._data[offset + 2]
        return (r + g + b) / 3 < threshold

    def _match_digit(self, features: List[int]) -> str:
        best, min_diff = '?', float('inf')
        for digit, template in self.TEMPLATES.items():
            diff = sum(f != t for f, t in zip(features, template))
            if diff < min_diff:
                min_diff, best = diff, digit
            if min_diff == 0:
                break
        return best


class ZimukuProvider(BaseProvider):
    """Zimuku subtitle provider with mirror fallback and automated WAF bypass."""

    name: str = "zimuku"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_url = self.config.get("base_url", settings.ZIMUKU_BASE_URL)
        self.fallback_urls = self.config.get("fallback_urls", settings.ZIMUKU_FALLBACKS)
        self.timeout = 4

        if settings.UPSTREAM_PROXY:
            self.session.proxies.update({
                "http": settings.UPSTREAM_PROXY,
                "https": settings.UPSTREAM_PROXY
            })

    def _fetch_page(self, url: str, referer: Optional[str] = None) -> Optional[requests.Response]:
        """GET request with automatic Yunsuo WAF bypass and JS redirect handling."""
        headers = {"Referer": referer} if referer else {}
        for _ in range(3):
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            except Exception as e:
                logger.debug(f"[Zimuku] GET {url} failed: {e}")
                return None

            # Detect Yunsuo WAF challenge page
            if b"security_verify_img" in resp.content or b"YunsuoAutoJump" in resp.content:
                logger.info(f"[Zimuku] WAF challenge detected for {url}, solving...")
                if self._solve_waf_captcha(url, resp.content):
                    continue
                else:
                    logger.warning("[Zimuku] Failed to solve WAF challenge")
                    return None

            # Handle JS location.replace redirect
            if resp.status_code == 200:
                m = re.search(r"window\.location\.replace\(['\"]([^'\"]+)['\"]\)", resp.text)
                if m:
                    redirect_url = m.group(1)
                    try:
                        resp = self.session.get(redirect_url, headers={"Referer": url}, timeout=self.timeout)
                    except Exception:
                        pass
                return resp

            if resp.status_code == 200:
                return resp
            break

        return None

    def _solve_waf_captcha(self, url: str, content: bytes) -> bool:
        """Answer the Yunsuo BMP captcha and submit security_verify_img verification."""
        try:
            soup = BeautifulSoup(content.decode("utf-8", "ignore"), "html.parser")
            img = soup.find("img")
            if not img:
                return False
            src = img.get("src", "")
            if "data:image/bmp;base64," not in src:
                return False

            b64_data = src.split("data:image/bmp;base64,", 1)[1]
            code = ZimukuBmpSolver(b64_data).recognize()
            logger.info(f"[Zimuku] Recognized BMP captcha code: {code}")

            parsed = urllib.parse.urlparse(url)
            srcurl_hex = "".join(f"{ord(c):x}" for c in url)
            self.session.cookies.set("srcurl", srcurl_hex, domain=parsed.netloc, path="/")

            code_hex = "".join(f"{ord(c):x}" for c in code)
            verify_url = f"{parsed.scheme}://{parsed.netloc}/?security_verify_img={code_hex}"
            self.session.get(verify_url, headers={"Referer": url}, timeout=self.timeout)
            return True
        except Exception as e:
            logger.error(f"[Zimuku] Captcha bypass error: {e}")
            return False

    def search(self, meta: VideoQueryMeta) -> List[SubtitleCandidate]:
        """Search Zimuku for matching subtitles using tailored queries."""
        search_queries = self._build_queries(meta)
        if not search_queries:
            return []

        all_endpoints = [self.base_url] + [u for u in self.fallback_urls if u != self.base_url]
        candidates: List[SubtitleCandidate] = []
        seen_ids = set()

        for domain in all_endpoints:
            found_any = False
            for query_str in search_queries:
                search_url = f"{domain}/search?q={urllib.parse.quote(query_str)}"
                logger.info(f"[Zimuku] Searching {search_url}...")
                resp = self._fetch_page(search_url)
                if not resp or resp.status_code != 200:
                    continue

                items = self._parse_works_from_search(resp.content, domain, meta)
                if items:
                    for sub in items:
                        if sub.id not in seen_ids:
                            seen_ids.add(sub.id)
                            candidates.append(sub)
                    found_any = True

                # If season-specific query succeeded, don't need broad search
                if found_any and meta.is_tv and meta.season:
                    break

            if candidates:
                break

        return candidates

    def _build_queries(self, meta: VideoQueryMeta) -> List[str]:
        """Build prioritized search queries for Zimuku."""
        queries = []
        clean_title = meta.title.strip() if meta.title else ""

        if meta.is_tv and meta.season:
            cn_s = to_cn_season(meta.season)
            if clean_title:
                queries.append(f"{clean_title} {cn_s}")
                queries.append(f"{clean_title} Season {meta.season}")
                queries.append(clean_title)
            if meta.season == 1 and meta.imdb_id:
                queries.append(meta.imdb_id)
        else:
            if clean_title:
                if meta.year:
                    queries.append(f"{clean_title} {meta.year}")
                queries.append(clean_title)
            if meta.imdb_id:
                queries.append(meta.imdb_id)

        # Deduplicate
        seen = set()
        deduped = []
        for q in queries:
            if q and q not in seen:
                seen.add(q)
                deduped.append(q)
        return deduped

    def _parse_works_from_search(
        self,
        html_content: bytes,
        domain: str,
        meta: VideoQueryMeta
    ) -> List[SubtitleCandidate]:
        """Parse work items from search results and extract subtitle rows from matching works."""
        soup = BeautifulSoup(html_content.decode("utf-8", "ignore"), "html.parser")
        work_divs = soup.select("div.item")
        candidates: List[SubtitleCandidate] = []

        for div in work_divs[:3]:  # Top 3 matching works
            a = div.select_one("div.title p.tt a")
            if not a:
                continue

            work_title = a.get_text(strip=True)
            work_href = a.get("href", "")
            if not work_href:
                continue

            # Check season match for TV series
            if meta.is_tv and meta.season:
                cn_s = to_cn_season(meta.season)
                s_tokens = [cn_s, f"第{meta.season}季", f"Season {meta.season}", f"S{meta.season:02d}"]
                # If search returned multiple season entries, pick the right season
                if not any(token.lower() in work_title.lower() for token in s_tokens) and len(work_divs) > 1:
                    continue

            work_url = urllib.parse.urljoin(domain, work_href)
            work_resp = self._fetch_page(work_url)
            if not work_resp or work_resp.status_code != 200:
                continue

            subs = self._parse_subtitles_from_work(work_resp.content, domain, meta)
            candidates.extend(subs)

        return candidates

    def _parse_subtitles_from_work(
        self,
        html_content: bytes,
        domain: str,
        meta: VideoQueryMeta
    ) -> List[SubtitleCandidate]:
        """Extract subtitles from work page table."""
        soup = BeautifulSoup(html_content.decode("utf-8", "ignore"), "html.parser")
        table = soup.select_one("div.subs.box.clearfix tbody")
        if not table:
            return []

        candidates = []
        rows = table.find_all("tr")

        for row in rows:
            sub_a = row.find("a")
            if not sub_a or not sub_a.get("href"):
                continue

            title = sub_a.get_text(strip=True)
            href = sub_a["href"]
            detail_url = urllib.parse.urljoin(domain, href)

            m_id = re.search(r"/detail/(\d+)\.html", href)
            sub_id = m_id.group(1) if m_id else href.split("/")[-1].replace(".html", "")

            # Episode filtering for TV series
            if meta.is_tv and meta.episode is not None:
                matches_ep, is_collection = is_episode_match(title, meta.season, meta.episode)
                if not matches_ep:
                    continue
            else:
                is_collection = False

            tags = SubtitleTags(provider=self.name, collection=is_collection)

            # Languages
            lang_td = row.find("td", class_="tac lang")
            if lang_td:
                for img in lang_td.find_all("img"):
                    img_title = img.get("title", "") or img.get("alt", "")
                    if "双语" in img_title or "双语" in title:
                        tags.bilingual = True
                    if "简体" in img_title or "chs" in title.lower() or "简" in title:
                        if "chs" not in tags.lang:
                            tags.lang.append("chs")
                    if "繁体" in img_title or "cht" in title.lower() or "繁" in title:
                        if "cht" not in tags.lang:
                            tags.lang.append("cht")

            if "双语" in title or "中英" in title:
                tags.bilingual = True
            if not tags.lang:
                tags.lang.append("chs")

            # Formats
            fmt_span = row.find("span", class_="label-info")
            if fmt_span:
                for f in fmt_span.get_text(strip=True).lower().split("/"):
                    f_clean = f.strip()
                    if f_clean and f_clean not in tags.fmt:
                        tags.fmt.append(f_clean)

            # Rating stars
            rate_val = 0.0
            star_i = row.find("i", class_=lambda c: c and "rating-star" in c)
            if star_i:
                star_title = star_i.get("title", "")
                m_score = re.search(r"(\d+(?:\.\d+)?)", star_title)
                if m_score:
                    rate_val = float(m_score.group(1)) / 2.0

            # Downloads count
            dl_count = 0
            tds = row.find_all("td")
            if len(tds) >= 5:
                dl_td = tds[4] if len(tds) >= 6 else tds[3]
                m_dl = re.search(r"(\d+)", dl_td.get_text(strip=True))
                if m_dl:
                    dl_count = int(m_dl.group(1))

            cand = SubtitleCandidate(
                id=sub_id,
                provider=self.name,
                title=title,
                page_url=detail_url,
                tags=tags,
                rate=rate_val,
                downloads_count=dl_count
            )
            candidates.append(cand)

        return candidates

    def download(self, candidate: SubtitleCandidate) -> Tuple[Optional[bytes], str]:
        """Download subtitle archive from Zimuku mirrors."""
        logger.info(f"[Zimuku] Fetching detail page {candidate.page_url}")
        resp = self._fetch_page(candidate.page_url)
        if not resp or resp.status_code != 200:
            return None, ""

        parsed_domain = f"{urllib.parse.urlparse(candidate.page_url).scheme}://{urllib.parse.urlparse(candidate.page_url).netloc}"
        soup = BeautifulSoup(resp.content.decode("utf-8", "ignore"), "html.parser")
        dl_sub = soup.find("li", class_="dlsub")
        if not dl_sub or not dl_sub.a:
            return None, ""

        dl_url = urllib.parse.urljoin(parsed_domain, dl_sub.a["href"])
        dl_page_resp = self._fetch_page(dl_url, referer=candidate.page_url)
        if not dl_page_resp or dl_page_resp.status_code != 200:
            return None, ""

        dl_soup = BeautifulSoup(dl_page_resp.content.decode("utf-8", "ignore"), "html.parser")
        links_box = dl_soup.find("div", class_="clearfix")
        if not links_box:
            return None, ""

        links = links_box.find_all("a", href=True)
        for a in links:
            file_url = urllib.parse.urljoin(parsed_domain, a["href"])
            try:
                file_resp = self.session.get(file_url, headers={"Referer": dl_url}, timeout=25)
                if file_resp.status_code == 200 and len(file_resp.content) >= FILE_MIN_SIZE:
                    filename = self.extract_filename_from_response(
                        file_resp, default=f"zimuku_{candidate.id}.zip"
                    )
                    return file_resp.content, filename
            except Exception as e:
                logger.debug(f"[Zimuku] Mirror download error: {e}")
                continue

        return None, ""
