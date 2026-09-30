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

        # 1. Provider health & rate-limit check
        if candidate.provider == "subhd":
            try:
                from ..providers.subhd import SubhdProvider
                if SubhdProvider.is_rate_limited():
                    score -= 2000.0
            except Exception:
                pass

        # 2. Season conflict check for TV series
        has_season_conflict = False
        cand_seasons = set()
        if query.is_tv and query.season is not None:
            from ..providers.base import extract_seasons_from_text
            cand_seasons = extract_seasons_from_text(candidate.title)
            if cand_seasons and query.season not in cand_seasons:
                has_season_conflict = True

        # IMDb exact match bonus (suppressed if TV series has conflicting season)
        if candidate.tags.imdb_matched and not has_season_conflict:
            score += 1000.0

        # 3. Release year verification
        if query.year:
            cand_years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", title_lower)]
            cand_years = [y for y in cand_years if y not in (1080, 2160)]
            if cand_years:
                if any(abs(y - query.year) <= 1 for y in cand_years):
                    score += 150.0
                elif not candidate.tags.imdb_matched:
                    score -= 1500.0

        # 4. Episode matching for TV series
        if query.is_tv and query.episode is not None:
            ep = query.episode
            s = query.season

            if has_season_conflict:
                # Heavy penalty if candidate title explicitly indicates a different season!
                score -= 5000.0
            elif cand_seasons and s in cand_seasons:
                score += 400.0  # Explicit season match bonus

            # Extract all explicit episode numbers from candidate title
            eps = []
            p1 = re.findall(r"(?:^|[^a-zA-Z0-9]|s\d{1,2})[eE][pP]?(\d{1,3})(?!\d)", title_lower)
            eps.extend(int(x) for x in p1)
            p2 = re.findall(r"第\s*(\d{1,3})\s*[集话話]", title_lower)
            eps.extend(int(x) for x in p2)
            found_eps = sorted(set(eps))

            matched_ep = ep in found_eps

            # Check season match
            season_matches = False
            if s is not None:
                season_pats = [
                    rf"\bs0*{s}\b",
                    rf"\bs0*{s}[^\d]",
                    rf"season\s*0*{s}\b",
                    rf"第\s*0*{s}\s*季"
                ]
                season_matches = any(re.search(pat, title_lower) for pat in season_pats) or (s in cand_seasons)

            # Check if mentions a conflicting other episode
            has_conflicting_ep = False
            if found_eps:
                if ep not in found_eps:
                    range_match = re.search(r"\b[eE]?(\d{1,3})\s*[-~至到]\s*[eE]?(\d{1,3})\b", title_lower)
                    if range_match:
                        start_ep, end_ep = int(range_match.group(1)), int(range_match.group(2))
                        if not (start_ep <= ep <= end_ep):
                            has_conflicting_ep = True
                    else:
                        has_conflicting_ep = True

            is_season_pack = (
                candidate.tags.collection
                or bool(re.search(r"全|合集|pack|complete|\b\d+-\d+\b|全部|整季|季全|全集", title_lower))
                or (season_matches and not matched_ep and not has_conflicting_ep)
            )

            if is_season_pack and (season_matches or s is None):
                # Season pack / collection covering all episodes (prioritized as requested)
                score += 1150.0
            elif matched_ep and not has_season_conflict:
                # Direct episode match
                score += 1000.0
            elif has_conflicting_ep:
                # Specific other episode
                score -= 600.0
            else:
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

        # 6. Video quality & release group matching
        if any(k in title_lower for k in ("2160p", "4k", "uhd")):
            score += 80.0
        elif "1080p" in title_lower:
            score += 60.0
        elif "720p" in title_lower:
            score += 20.0

        if any(k in title_lower for k in ("bluray", "bdrip", "remux")):
            score += 60.0
        elif any(k in title_lower for k in ("web-dl", "webrip", "amzn", "nf", "hulu")):
            score += 50.0
        elif "hdtv" in title_lower:
            score += 10.0

        # If query has filename from player, reward matching characteristics
        if query.filename:
            fn_lower = query.filename.lower()
            # Match release groups (e.g. TrollHD, Sauron, CasStudio, YYeTs, etc.)
            for grp in ("trollhd", "sauron", "casstudio", "yyets", "fix", "chd", "wiki", "ntb", "dim", "ctrlhd"):
                if grp in title_lower and grp in fn_lower:
                    score += 120.0
                    break
            # Match resolution agreement
            for res in ("2160p", "1080p", "720p"):
                if res in title_lower and res in fn_lower:
                    score += 60.0
                    break
            # Match source agreement
            for src_tag in ("bluray", "web-dl", "webrip", "hdtv"):
                if src_tag in title_lower and src_tag in fn_lower:
                    score += 40.0
                    break

        return score

