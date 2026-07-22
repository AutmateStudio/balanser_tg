"""Скоринг кандидатов по последним постам (intent / anti-spam / premium)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from discovery_api.lead_intent.keywords import (
    JOB_BOARD_KEYWORDS,
    LEAD_KEYWORDS,
    PREMIUM_URL_MARKERS,
    SCORE_COMMUNITY_PENALTY,
    SCORE_LEAD_HIT,
    SCORE_MAX,
    SCORE_MIN,
    SCORE_PREMIUM_URL,
    SCORE_SPAM_HIT,
    SCORE_TZ_FILE,
    SPAM_KEYWORDS,
    TZ_FILE_EXTENSIONS,
)


def _clamp_score(value: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(value)))


def _text_of(msg: Any) -> str:
    if msg is None:
        return ""
    if isinstance(msg, dict):
        for key in ("text", "message", "raw_text"):
            val = msg.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return ""
    for attr in ("message", "text", "raw_text"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _file_ext(msg: Any) -> Optional[str]:
    if isinstance(msg, dict):
        ext = msg.get("file_ext") or msg.get("ext")
        if isinstance(ext, str) and ext.strip():
            e = ext.strip().lower()
            return e if e.startswith(".") else f".{e}"
        name = msg.get("file_name") or msg.get("filename")
        if isinstance(name, str) and "." in name:
            return "." + name.rsplit(".", 1)[-1].lower()
        return None
    f = getattr(msg, "file", None)
    if f is None:
        return None
    ext = getattr(f, "ext", None)
    if isinstance(ext, str) and ext.strip():
        e = ext.strip().lower()
        return e if e.startswith(".") else f".{e}"
    name = getattr(f, "name", None)
    if isinstance(name, str) and "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return None


def _hits_in_text(text: str, keywords: Iterable[str]) -> List[str]:
    lower = (text or "").lower()
    if not lower:
        return []
    found: List[str] = []
    for kw in keywords:
        if kw and kw.lower() in lower:
            found.append(kw)
    return found


@dataclass
class ScoreResult:
    lead_score: int
    lead_probability: float
    intent_hits: List[str] = field(default_factory=list)
    spam_hits: List[str] = field(default_factory=list)
    premium_hits: List[str] = field(default_factory=list)
    tz_files: int = 0
    is_job_board: bool = False
    is_community: bool = False
    is_client_base: bool = False
    breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_score": self.lead_score,
            "lead_probability": self.lead_probability,
            "intent_hits": list(self.intent_hits),
            "spam_hits": list(self.spam_hits),
            "premium_hits": list(self.premium_hits),
            "tz_files": self.tz_files,
            "is_job_board": self.is_job_board,
            "is_community": self.is_community,
            "is_client_base": self.is_client_base,
            "breakdown": dict(self.breakdown),
        }


def score_messages(
    messages: Sequence[Any],
    *,
    niche: str = "",
    is_broadcast: bool = False,
    is_megagroup: bool = False,
    community_ratio: Optional[float] = None,
    community_threshold: float = 0.8,
) -> ScoreResult:
    """Считает lead_score по текстам/файлам последних постов."""
    score = 0
    intent_hits: Set[str] = set()
    spam_hits: Set[str] = set()
    premium_hits: Set[str] = set()
    job_hits: Set[str] = set()
    tz_files = 0
    lead_msg_count = 0
    spam_msg_count = 0

    for msg in messages or []:
        text = _text_of(msg)
        leads = _hits_in_text(text, LEAD_KEYWORDS)
        spams = _hits_in_text(text, SPAM_KEYWORDS)
        jobs = _hits_in_text(text, JOB_BOARD_KEYWORDS)
        premiums = _hits_in_text(text, PREMIUM_URL_MARKERS)

        if leads:
            lead_msg_count += 1
            score += SCORE_LEAD_HIT
            intent_hits.update(leads)
        if spams:
            spam_msg_count += 1
            score += SCORE_SPAM_HIT
            spam_hits.update(spams)
        if jobs:
            job_hits.update(jobs)
        if premiums:
            score += SCORE_PREMIUM_URL
            premium_hits.update(premiums)

        ext = _file_ext(msg)
        if ext and ext.lower() in TZ_FILE_EXTENSIONS:
            tz_files += 1
            score += SCORE_TZ_FILE

    is_community = False
    if community_ratio is not None and community_ratio >= community_threshold:
        is_community = True
        score += SCORE_COMMUNITY_PENALTY
    elif spam_msg_count > 0 and spam_msg_count >= max(1, lead_msg_count * 2):
        # spam-dominant лента
        is_community = True
        score += SCORE_COMMUNITY_PENALTY // 2

    is_job_board = len(job_hits) >= 2 or (len(job_hits) >= 1 and lead_msg_count >= 2)
    is_client_base = bool(is_broadcast and intent_hits)

    # лёгкий буст, если ниша встречается вместе с интентом
    niche_l = (niche or "").strip().lower()
    if niche_l and intent_hits:
        for msg in messages or []:
            if niche_l in _text_of(msg).lower():
                score += 5
                break

    if is_megagroup and is_community:
        # уже учли штраф
        pass

    final = _clamp_score(score)
    return ScoreResult(
        lead_score=final,
        lead_probability=round(final / 100.0, 4),
        intent_hits=sorted(intent_hits),
        spam_hits=sorted(spam_hits),
        premium_hits=sorted(premium_hits),
        tz_files=tz_files,
        is_job_board=is_job_board,
        is_community=is_community,
        is_client_base=is_client_base,
        breakdown={
            "raw_score": score,
            "lead_msg_count": lead_msg_count,
            "spam_msg_count": spam_msg_count,
            "job_hits": sorted(job_hits),
            "community_ratio": community_ratio,
            "is_broadcast": is_broadcast,
            "is_megagroup": is_megagroup,
        },
    )


def merge_comment_score(base: ScoreResult, comments: Sequence[Any], *, niche: str = "") -> ScoreResult:
    """Добавляет сигналы из GetReplies к уже посчитанному скору постов."""
    if not comments:
        return base
    extra = score_messages(comments, niche=niche, is_broadcast=True)
    combined_score = _clamp_score(base.lead_score + max(0, extra.lead_score // 2))
    intent = sorted(set(base.intent_hits) | set(extra.intent_hits))
    spam = sorted(set(base.spam_hits) | set(extra.spam_hits))
    premium = sorted(set(base.premium_hits) | set(extra.premium_hits))
    is_client_base = base.is_client_base or bool(extra.intent_hits)
    return ScoreResult(
        lead_score=combined_score,
        lead_probability=round(combined_score / 100.0, 4),
        intent_hits=intent,
        spam_hits=spam,
        premium_hits=premium,
        tz_files=base.tz_files + extra.tz_files,
        is_job_board=base.is_job_board or extra.is_job_board,
        is_community=base.is_community,
        is_client_base=is_client_base,
        breakdown={
            **dict(base.breakdown),
            "comments_lead_score": extra.lead_score,
            "comments_intent_hits": extra.intent_hits,
        },
    )
