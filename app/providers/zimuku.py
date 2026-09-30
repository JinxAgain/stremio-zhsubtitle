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
    extract_meta_from_filename,
    extract_seasons_from_text,
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


_unhealthy_domains: Dict[str, float] = {}


class ZimukuProvider(BaseProvider):
    """Zimuku subtitle provider with mirror fallback and automated WAF bypass."""

    name: str = "zimuku"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_url = self.config.get("base_url", settings.ZIMUKU_BASE_URL)
        self.fallback_urls = self.config.get("fallback_urls", settings.ZIMUKU_FALLBACKS)
        self.timeout = 12

        if settings.UPSTREAM_PROXY:
            self.session.proxies.update({
                "http": settings.UPSTREAM_PROXY,
                "https": settings.UPSTREAM_PROXY
            })

    @classmethod
    def is_domain_healthy(cls, domain: str) -> bool:
        """Check if a mirror domain is currently healthy or in cooldown."""
        import time
        return _unhealthy_domains.get(domain, 0) < time.time()

    @classmethod
    def mark_domain_unhealthy(cls, domain: str, cooldown_secs: int = 600):
        """Put a failing or timed-out mirror domain into temporary cooldown."""
        import time
        _unhealthy_domains[domain] = time.time() + cooldown_secs
        logger.warning(f"[Zimuku] Marking mirror {domain} as unhealthy for {cooldown_secs}s")

    def _fetch_page(self, url: str, referer: Optional[str] = None) -> Optional[requests.Response]:
        """GET request with automatic Yunsuo WAF bypass and JS redirect handling."""
        parsed = urllib.parse.urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        if not self.is_domain_healthy(domain):
            return None

        headers = {"Referer": referer} if referer else {}
        timeout = self.timeout if "zmk.pw" in domain else 4

        for _ in range(3):
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"[Zimuku] GET {url} failed ({type(e).__name__}): {e}")
                if "zmk.pw" not in domain:
                    self.mark_domain_unhealthy(domain, cooldown_secs=600)
                return None
            except Exception as e:
                logger.warning(f"[Zimuku] GET {url} failed: {e}")
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
                        resp = self.session.get(redirect_url, headers={"Referer": url}, timeout=timeout)
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

    @staticmethod
    def _normalize_url(domain: str, href: str) -> str:
        """Force any relative or protocol-relative Zimuku URL to stay on the active mirror domain."""
        parsed = urllib.parse.urlparse(href)
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"
        if not path.startswith("/"):
            path = "/" + path
        return f"{domain}{path}"

    def search(self, meta: VideoQueryMeta) -> List[SubtitleCandidate]:
        """Search Zimuku for matching subtitles using tailored queries."""
        search_queries = self._build_queries(meta)
        if not search_queries:
            return []

        all_endpoints = [
            d for d in [self.base_url] + [u for u in self.fallback_urls if u != self.base_url]
            if self.is_domain_healthy(d)
        ]
        if not all_endpoints:
            all_endpoints = [self.base_url]
        candidates: List[SubtitleCandidate] = []
        seen_ids = set()

        for domain in all_endpoints:
            found_any = False
            domain_dead = False
            for query_str in search_queries:
                is_imdb_query = query_str.startswith("tt")
                search_url = f"{domain}/search?q={urllib.parse.quote(query_str)}"
                logger.info(f"[Zimuku] Searching {search_url}...")
                resp = self._fetch_page(search_url)
                if not resp:
                    # If this mirror timed out or cannot connect at all, fail fast to the next mirror
                    domain_dead = True
                    break
                if resp.status_code != 200:
                    continue

                new_found = 0
                # 1. Parse direct subtitles table from search page
                table_items = self._parse_subtitles_from_search_table(resp.content, domain, meta)
                if table_items:
                    for sub in table_items:
                        if is_imdb_query:
                            sub.tags.imdb_matched = True
                        if sub.id not in seen_ids:
                            seen_ids.add(sub.id)
                            candidates.append(sub)
                            new_found += 1
                    found_any = True

                # 2. Check work items (top series or film cards)
                work_items = self._parse_works_from_search(resp.content, domain, meta)
                if work_items:
                    for sub in work_items:
                        if is_imdb_query:
                            sub.tags.imdb_matched = True
                        if sub.id not in seen_ids:
                            seen_ids.add(sub.id)
                            candidates.append(sub)
                            new_found += 1
                    found_any = True

                # If exact IMDb query succeeded, stop searching to avoid noisy/false title fallbacks!
                if is_imdb_query and new_found > 0:
                    break

                # If season-specific query succeeded, don't need broad search
                if found_any and meta.is_tv and meta.season:
                    break

            if candidates:
                break
            if domain_dead:
                continue

        return candidates

    def _build_queries(self, meta: VideoQueryMeta) -> List[str]:
        """Build prioritized search queries for Zimuku."""
        queries = []
        clean_title = meta.title.strip() if meta.title else ""
        if not clean_title and meta.filename:
            fn_title, fn_year, fn_s, fn_ep = extract_meta_from_filename(meta.filename)
            if fn_title:
                clean_title = fn_title
                if not meta.year and fn_year:
                    meta.year = fn_year

        is_tt_imdb = bool(meta.imdb_id and meta.imdb_id.startswith("tt"))

        if meta.is_tv and meta.season:
            cn_s = to_cn_season(meta.season)
            if meta.season > 1:
                # For Season > 1, Zimuku does NOT support 'IMDb + Season' or 'Title S04' search.
                # Zimuku strictly indexes TV show work cards with Chinese season titles (e.g. 'Ted Lasso 第四季').
                # Therefore, 'Title + 第X季' is the only valid search query.
                if clean_title:
                    queries.append(f"{clean_title} {cn_s}")
            else:
                # For Season 1, title + S01, title + 第一季, or exact IMDb ID
                if clean_title:
                    queries.append(f"{clean_title} S01")
                    queries.append(f"{clean_title} 第一季")
                if is_tt_imdb:
                    queries.append(meta.imdb_id)
                if clean_title:
                    queries.append(f"{clean_title} Season 1")
                    queries.append(clean_title)
        else:
            # 1. Primary: Exact IMDb ID for movies
            if is_tt_imdb:
                queries.append(meta.imdb_id)
            # 2. Fallbacks: Title & Year
            if clean_title:
                if meta.year:
                    queries.append(f"{clean_title} {meta.year}")
                queries.append(clean_title)

        # Deduplicate
        seen = set()
        deduped = []
        for q in queries:
            q_clean = q.strip()
            if q_clean and q_clean not in seen:
                seen.add(q_clean)
                deduped.append(q_clean)
        return deduped

    def _parse_subtitles_from_search_table(
        self,
        html_content: bytes,
        domain: str,
        meta: VideoQueryMeta
    ) -> List[SubtitleCandidate]:
        """Extract subtitles directly from search results table without extra HTTP requests."""
        soup = BeautifulSoup(html_content.decode("utf-8", "ignore"), "html.parser")
        table = soup.select_one("table.tborder2") or soup.select_one("table.table")
        if not table:
            return []

        candidates = []
        rows = table.find_all("tr")

        for row in rows:
            first_td = row.find("td", class_="first")
            if not first_td:
                continue

            sub_a = first_td.find("a")
            if not sub_a or not sub_a.get("href"):
                continue

            title = sub_a.get("title") or sub_a.get_text(strip=True)
            href = sub_a["href"]
            m_id = re.search(r"/detail/(\d+)\.html", href)
            sub_id = m_id.group(1) if m_id else href.split("/")[-1].replace(".html", "")
            detail_url = f"{domain}/detail/{sub_id}.html"

            # Episode filtering for TV series
            if meta.is_tv and meta.episode is not None:
                matches_ep, is_collection = is_episode_match(title, meta.season, meta.episode)
                if not matches_ep:
                    continue
            else:
                is_collection = False

            tags = SubtitleTags(provider=self.name, collection=is_collection)

            # Languages
            tac_td = row.find("td", class_="tac")
            img_tags = first_td.find_all("img") + (tac_td.find_all("img") if tac_td else [])
            for img in img_tags:
                alt = f"{img.get('alt', '')} {img.get('title', '')}"
                if "双语" in alt:
                    tags.bilingual = True
                if "简体" in alt or "chs" in alt.lower():
                    if "chs" not in tags.lang:
                        tags.lang.append("chs")
                if "繁体" in alt or "cht" in alt.lower():
                    if "cht" not in tags.lang:
                        tags.lang.append("cht")

            if "双语" in title or "中英" in title:
                tags.bilingual = True
            if not tags.lang:
                tags.lang.append("chs")

            # Formats
            fmt_span = first_td.find("span", class_="label-info")
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
            last_td = row.find("td", class_="last")
            if last_td:
                m_dl = re.search(r"(\d+)", last_td.get_text(strip=True))
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
                work_seasons = extract_seasons_from_text(work_title)
                # If work title explicitly belongs to a different season, reject immediately!
                if work_seasons and meta.season not in work_seasons:
                    continue
                cn_s = to_cn_season(meta.season)
                s_tokens = [cn_s, f"第{meta.season}季", f"Season {meta.season}", f"S{meta.season:02d}", f"S{meta.season}"]
                if not (work_seasons and meta.season in work_seasons):
                    if not any(token.lower() in work_title.lower() for token in s_tokens) and len(work_divs) > 1:
                        continue

            work_url = self._normalize_url(domain, work_href)
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
            m_id = re.search(r"/detail/(\d+)\.html", href)
            sub_id = m_id.group(1) if m_id else href.split("/")[-1].replace(".html", "")
            detail_url = self._normalize_url(domain, href)

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
        """Download subtitle archive from Zimuku mirrors with failover."""
        orig_domain = (
            f"{urllib.parse.urlparse(candidate.page_url).scheme}://{urllib.parse.urlparse(candidate.page_url).netloc}"
            if candidate.page_url else self.base_url
        )
        all_domains = [
            d for d in [orig_domain] + [u for u in [self.base_url] + self.fallback_urls if u != orig_domain]
            if self.is_domain_healthy(d)
        ]
        if not all_domains:
            all_domains = [orig_domain]
        path = urllib.parse.urlparse(candidate.page_url).path if candidate.page_url else f"/detail/{candidate.id}.html"

        for domain in all_domains:
            detail_url = f"{domain}{path}"
            logger.info(f"[Zimuku] Fetching detail page {detail_url}")
            resp = self._fetch_page(detail_url)
            if not resp or resp.status_code != 200:
                logger.warning(f"[Zimuku] Detail page {detail_url} unavailable")
                continue

            soup = BeautifulSoup(resp.content.decode("utf-8", "ignore"), "html.parser")
            dl_sub = soup.find("li", class_="dlsub")
            if not dl_sub or not dl_sub.a:
                continue

            dl_url = self._normalize_url(domain, dl_sub.a["href"])
            dl_page_resp = self._fetch_page(dl_url, referer=detail_url)
            if not dl_page_resp or dl_page_resp.status_code != 200:
                continue

            dl_soup = BeautifulSoup(dl_page_resp.content.decode("utf-8", "ignore"), "html.parser")
            links_box = dl_soup.find("div", class_="clearfix")
            if not links_box:
                continue

            links = links_box.find_all("a", href=True)
            for a in links:
                file_url = self._normalize_url(domain, a["href"])
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
