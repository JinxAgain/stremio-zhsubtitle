"""Subtitle ranking and scoring engine."""

import re
from typing import List, Optional

from ..providers.base import SubtitleCandidate, VideoQueryMeta


class SubtitleScorer:
    """Ranks candidate subtitles based on video query metadata and user configuration."""

    @classmethod
    def rank_candidates(
        cls,
        candidates: List[SubtitleCandidate],
        query: VideoQueryMeta,
        lang_preference: str = "bilingual",  # "bilingual", "chs", "cht"
        max_results: int = 8
    ) -> List[SubtitleCandidate]:
        """Score each candidate and return top candidates sorted by score descending."""
        if not candidates:
            return []

        scored: List[SubtitleCandidate] = []
        for cand in candidates:
            cand.score = cls.calculate_score(cand, query, lang_preference)
            scored.append(cand)

        # Sort highest score first
        scored.sort(key=lambda c: (c.score, c.rate, c.downloads_count), reverse=True)

        # Deduplicate candidates with exact same page_url or title
        seen_keys = set()
        deduped = []
        for c in scored:
            key = (c.provider, c.id)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(c)

        return deduped[:max_results]

    @classmethod
    def calculate_score(
        cls,
        candidate: SubtitleCandidate,
        query: VideoQueryMeta,
        lang_pref: str
    ) -> float:
        """Calculate composite relevance and quality score for a subtitle candidate."""
        score = 0.0
        title_lower = candidate.title.lower()

        # 1. Episode matching for TV series
        if query.is_tv and query.episode is not None:
            ep = query.episode
            ep_patterns = [
                rf"\b[eE][pP]?0*{ep}\b",
                rf"s\d{{1,2}}e0*{ep}\b",
                rf"第\s*0*{ep}\s*[集话話]",
                rf"\[0*{ep}\]"
            ]
            matched_ep = any(re.search(pat, title_lower) for pat in ep_patterns)
            if matched_ep:
                score += 1000.0
            elif candidate.tags.collection or bool(re.search(r"全|合集|pack|complete|\b\d+-\d+\b|全部|整季|季全", title_lower)):
                # Season pack / collection subtitle containing target episode
                score += 700.0
            else:
                # Does not mention target episode
                score -= 200.0

        # 2. Language preference scoring
        tags = candidate.tags
        is_bilingual = tags.bilingual or "双语" in title_lower or "中英" in title_lower
        is_cht = "cht" in tags.lang or any(k in title_lower for k in ("繁体", "繁體", "繁中", "cht", "big5"))
        is_chs = "chs" in tags.lang or any(k in title_lower for k in ("简体", "简中", "chs", "gb"))

        if lang_pref == "bilingual":
            if is_bilingual:
                score += 600.0
            elif is_chs:
                score += 350.0
            elif is_cht:
                score += 300.0
        elif lang_pref == "cht":
            if is_cht:
                score += 600.0
            elif is_bilingual:
                score += 450.0
            elif is_chs:
                score += 200.0
        else:  # "chs"
            if is_chs and not is_cht:
                score += 600.0
            elif is_bilingual:
                score += 450.0
            elif is_cht:
                score += 200.0

        # 3. Format bonus
        for fmt in tags.fmt:
            f = fmt.lower()
            if f in ("ass", "ssa"):
                score += 30.0
            elif f == "srt":
                score += 20.0

        # 4. Source translation quality bonus
        for src in tags.source:
            s_lower = src.lower()
            if any(k in s_lower for k in ("official", "官方", "官译")):
                score += 100.0
            elif any(k in s_lower for k in ("reprint", "精修", "转载精修")):
                score += 80.0
            elif any(k in s_lower for k in ("original", "原创")):
                score += 60.0

        # 5. Rating and popularity bonus
        if candidate.rate > 0:
            score += min(candidate.rate * 10.0, 50.0)
        if candidate.downloads_count > 0:
            score += min(candidate.downloads_count / 50.0, 40.0)

        return score
