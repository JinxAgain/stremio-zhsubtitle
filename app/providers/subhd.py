"""SubHD (subhd.tv / subhd.me / subhd.one / subhd.cc) subtitle provider implementation."""

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from .base import (
    BaseProvider,
    SubtitleCandidate,
    SubtitleTags,
    VideoQueryMeta,
    is_episode_match,
)
from ..config import settings

logger = logging.getLogger(__name__)

SOURCE_BADGE_MAP = {
    "官方字幕": "official",
    "官方": "official",
    "官译": "official",
    "转载精修": "reprint",
    "精修": "reprint",
    "转载": "reprint",
    "原创翻译": "original",
    "原创": "original",
    "自翻": "original",
    "AI校对": "ai",
    "AI润色": "ai",
    "AI翻译": "ai",
    "机器翻译": "machine",
    "机翻": "machine"
}


class SubhdProvider(BaseProvider):
    """SubHD subtitle provider supporting IMDb ID queries and token-based download flow."""

    name: str = "subhd"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_url = self.config.get("base_url", settings.SUBHD_BASE_URL)
        self.fallback_urls = self.config.get("fallback_urls", settings.SUBHD_FALLBACKS)
        self.timeout = 4

        if settings.UPSTREAM_PROXY:
            self.session.proxies.update({
                "http": settings.UPSTREAM_PROXY,
                "https": settings.UPSTREAM_PROXY
            })

    def search(self, meta: VideoQueryMeta) -> List[SubtitleCandidate]:
        """Search SubHD for subtitles using prioritized queries."""
        search_queries = self._build_queries(meta)
        if not search_queries:
            return []

        all_endpoints = [self.base_url] + [u for u in self.fallback_urls if u != self.base_url]
        candidates: List[SubtitleCandidate] = []
        seen_sids = set()

        for domain in all_endpoints:
            found_any = False
            for query_str in search_queries:
                search_url = f"{domain}/search/{urllib.parse.quote(query_str)}"
                logger.info(f"[SubHD] Searching {search_url}...")
                try:
                    resp = self.session.get(search_url, timeout=self.timeout)
                    if resp.status_code == 200:
                        items = self._parse_search_results(resp.content, domain, meta)
                        if items:
                            for item in items:
                                if item.id not in seen_sids:
                                    seen_sids.add(item.id)
                                    candidates.append(item)
                            found_any = True
                except Exception as e:
                    logger.debug(f"[SubHD] Request failed for {search_url}: {e}")
                    continue

                # If specific query returned good matches, don't flood upstream
                if found_any and len(candidates) >= 5:
                    break

            if candidates:
                break

        return candidates

    def _build_queries(self, meta: VideoQueryMeta) -> List[str]:
        """Build prioritized search queries for SubHD."""
        queries = []
        if meta.is_tv and meta.season is not None:
            # 1. Exact episode with IMDb ID
            if meta.imdb_id and meta.episode is not None:
                queries.append(f"{meta.imdb_id} S{meta.season:02d}E{meta.episode:02d}")
            # 2. Season with IMDb ID
            if meta.imdb_id:
                queries.append(f"{meta.imdb_id} S{meta.season:02d}")
                queries.append(meta.imdb_id)
            # 3. Fallbacks with Title
            if meta.title:
                if meta.episode is not None:
                    queries.append(f"{meta.title} S{meta.season:02d}E{meta.episode:02d}")
                queries.append(f"{meta.title} S{meta.season:02d}")
                queries.append(meta.title)
        else:
            # Movie search
            if meta.imdb_id:
                queries.append(meta.imdb_id)
            if meta.title:
                if meta.year:
                    queries.append(f"{meta.title} {meta.year}")
                queries.append(meta.title)

        # Deduplicate
        seen = set()
        deduped = []
        for q in queries:
            q_clean = q.strip()
            if q_clean and q_clean not in seen:
                seen.add(q_clean)
                deduped.append(q_clean)
        return deduped

    def _parse_search_results(
        self,
        html_content: bytes,
        domain: str,
        meta: VideoQueryMeta
    ) -> List[SubtitleCandidate]:
        """Parse subtitle cards from SubHD search HTML."""
        soup = BeautifulSoup(html_content.decode("utf-8", "ignore"), "html.parser")
        items: List[SubtitleCandidate] = []
        seen_sids = set()

        blocks = soup.select("div.bg-white.shadow-sm.rounded-3.mb-4, div.bg-white.shadow-sm.rounded-3.mb-5")
        for block in blocks:
            # Find detail link
            a_tags = block.find_all("a", href=True)
            sid = None
            for a in a_tags:
                m = re.search(r"^/a/([0-9A-Za-z]+)", a.get("href", ""))
                if m:
                    sid = m.group(1)
                    break

            if not sid or sid in seen_sids:
                continue

            seen_sids.add(sid)

            # Extract title components
            head_a = block.select_one("div.f16 a")
            view_a = block.select_one("div.view-text a") or block.select_one("div.text-secondary a")
            head_text = head_a.get_text(strip=True) if head_a else ""
            view_text = view_a.get_text(strip=True) if view_a else ""

            if view_text and view_text != head_text:
                title = f"{head_text} - {view_text}" if head_text else view_text
            else:
                title = view_text or head_text or f"SubHD Subtitle {sid}"

            # Filter by episode if looking for specific TV episode
            if meta.is_tv and meta.episode is not None:
                matches_ep, is_collection = is_episode_match(title, meta.season, meta.episode)
                if not matches_ep:
                    continue
            else:
                is_collection = False

            tags, dl_count = self._parse_tags_from_card(block)
            tags.collection = is_collection
            page_url = f"{domain}/a/{sid}"

            cand = SubtitleCandidate(
                id=sid,
                provider=self.name,
                title=title,
                page_url=page_url,
                tags=tags,
                downloads_count=dl_count
            )
            items.append(cand)

        return items

    def _parse_tags_from_card(self, block) -> Tuple[SubtitleTags, int]:
        """Extract metadata tags and download count from card badges."""
        tags = SubtitleTags(provider=self.name)
        dl_count = 0
        spans = block.find_all("span")

        for span in spans:
            text = span.get_text(strip=True)
            if not text:
                continue

            # Source badges
            for cn, key in SOURCE_BADGE_MAP.items():
                if cn in text and key not in tags.source:
                    tags.source.append(key)
                    break

            # Language badges
            if ("简体" in text or "简中" in text) and "chs" not in tags.lang:
                tags.lang.append("chs")
            if ("繁体" in text or "繁中" in text) and "cht" not in tags.lang:
                tags.lang.append("cht")
            if "双语" in text or "中英" in text:
                tags.bilingual = True

            # Format badges
            for fmt in ("ass", "srt", "ssa", "vtt"):
                if fmt.upper() in text.upper() and fmt not in tags.fmt:
                    tags.fmt.append(fmt)

            # Download counts
            if span.find("i", class_=lambda c: c and ("download" in c or "cloud" in c)):
                m_num = re.search(r"(\d+)", text)
                if m_num:
                    dl_count = int(m_num.group(1))

        if not tags.lang:
            tags.lang.append("chs")

        # Fansub / author link
        zu_el = block.select_one('a[href^="/zu/"]')
        if zu_el:
            tags.fansub = zu_el.get_text(strip=True)

        return tags, dl_count

    def download(self, candidate: SubtitleCandidate) -> Tuple[Optional[bytes], str]:
        """Download subtitle file from SubHD using the prepare-download -> down API token flow."""
        sid = candidate.id
        domain = f"{urllib.parse.urlparse(candidate.page_url).scheme}://{urllib.parse.urlparse(candidate.page_url).netloc}"
        page_url = candidate.page_url or f"{domain}/a/{sid}"

        logger.info(f"[SubHD] Preparing download for sid '{sid}' from {page_url}")

        try:
            # Step 1: POST prepare-download
            prep_resp = self.session.post(
                f"{domain}/api/sub/prepare-download",
                json={"sid": sid},
                headers={
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest"
                },
                timeout=self.timeout
            )
            if prep_resp.status_code != 200:
                logger.warning(f"[SubHD] prepare-download HTTP {prep_resp.status_code}")
                return None, ""

            prep_data = prep_resp.json()
            if not prep_data.get("success"):
                logger.warning(f"[SubHD] prepare-download failed: {prep_data.get('msg')}")
                return None, ""

            down_path = prep_data.get("url") or f"/down/{sid}"
            down_url = down_path if down_path.startswith("http") else f"{domain}{down_path}"

            # Step 2: Visit temp page (essential before requesting /api/sub/down)
            temp_resp = self.session.get(down_url, headers={"Referer": page_url}, timeout=self.timeout)
            if temp_resp.status_code != 200:
                return None, ""

            # Step 3: POST /api/sub/down
            api_resp = self.session.post(
                f"{domain}/api/sub/down",
                json={"sid": sid, "cap": ""},
                headers={
                    "Referer": down_url,
                    "X-Requested-With": "XMLHttpRequest"
                },
                timeout=self.timeout
            )
            if api_resp.status_code != 200:
                return None, ""

            api_data = api_resp.json()
            if not api_data.get("success") or not api_data.get("url"):
                return None, ""

            file_url = api_data.get("url")
            if not file_url.startswith("http"):
                file_url = f"{domain}{file_url}"

            # Step 4: Fetch actual archive bytes
            file_resp = self.session.get(file_url, headers={"Referer": down_url}, timeout=25)
            if file_resp.status_code == 200:
                filename = self.extract_filename_from_response(file_resp, default=f"subhd_{sid}.zip")
                return file_resp.content, filename

        except Exception as e:
            logger.error(f"[SubHD] Download error for {sid}: {e}")

        return None, ""
