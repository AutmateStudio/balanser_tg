"""Предыдущая модель скоринга (релевантность + источник + глубина). Оставлена для отладки."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set


@dataclass(frozen=True)
class GroupDiscoveryScore:
    score_total: int
    breakdown: Dict[str, float]
    extracted_signals: Dict[str, Any]


@dataclass(frozen=True)
class ChannelDiscoveryScore:
    score_total: int
    breakdown: Dict[str, float]
    extracted_signals: Dict[str, Any]


def score_discovered_group(
    *,
    title: str,
    username: Optional[str],
    participants_count: Optional[int],
    messages_30d: Optional[int],
    query: str,
    matched_seed: Optional[str],
    depth: int,
    source: str,
) -> GroupDiscoveryScore:
    title_text = (title or "").strip().lower()
    username_text = (username or "").strip().lower()
    query_tokens = _tokenize(query)
    seed_tokens = _tokenize(matched_seed or "")
    union_name_tokens = _tokenize(title_text) | _tokenize(username_text)

    overlap_query = _overlap_ratio(query_tokens, union_name_tokens)
    overlap_seed = _overlap_ratio(seed_tokens, union_name_tokens)
    exact_query_in_title = 1.0 if query and query.lower() in title_text else 0.0
    relevance_score = min(30.0, overlap_query * 18.0 + overlap_seed * 8.0 + exact_query_in_title * 4.0)

    members = max(0, int(participants_count or 0))
    if members >= 300000:
        members_score = 20.0
    elif members >= 120000:
        members_score = 18.0
    elif members >= 50000:
        members_score = 16.0
    elif members >= 20000:
        members_score = 13.0
    elif members >= 5000:
        members_score = 9.0
    elif members >= 1000:
        members_score = 6.0
    elif members > 0:
        members_score = 3.0
    else:
        members_score = 0.0

    source_score = {"contacts_search": 15.0, "global_search": 13.0, "recommendation": 10.0}.get(source, 8.0)
    depth_score = max(0.0, 10.0 - max(0, int(depth)) * 2.0)

    messages_count = max(0, int(messages_30d or 0))
    if messages_count >= 600:
        activity_score = 25.0
    elif messages_count >= 300:
        activity_score = 22.0
    elif messages_count >= 150:
        activity_score = 18.0
    elif messages_count >= 80:
        activity_score = 13.0
    elif messages_count >= 40:
        activity_score = 9.0
    elif messages_count >= 10:
        activity_score = 5.0
    elif messages_count > 0:
        activity_score = 2.0
    else:
        activity_score = 0.0

    total = int(max(0.0, min(100.0, relevance_score + members_score + source_score + depth_score + activity_score)))
    return GroupDiscoveryScore(
        score_total=total,
        breakdown={
            "relevance": relevance_score,
            "members": members_score,
            "source": source_score,
            "depth": depth_score,
            "activity": activity_score,
        },
        extracted_signals={
            "query_overlap": overlap_query,
            "seed_overlap": overlap_seed,
            "query_exact_in_title": bool(exact_query_in_title),
            "members_count": members,
            "messages_30d": messages_count,
            "depth": depth,
            "source": source,
        },
    )


def score_discovered_channel(
    *,
    title: str,
    username: Optional[str],
    participants_count: Optional[int],
    query: str,
    depth: int,
    source: str,
) -> ChannelDiscoveryScore:
    title_text = (title or "").strip().lower()
    username_text = (username or "").strip().lower()
    query_tokens = _tokenize(query)
    name_tokens = _tokenize(title_text) | _tokenize(username_text)
    overlap = _overlap_ratio(query_tokens, name_tokens)
    exact_query = 1.0 if query and query.lower() in title_text else 0.0
    relevance_score = min(45.0, overlap * 35.0 + exact_query * 10.0)

    members = max(0, int(participants_count or 0))
    if members >= 800000:
        members_score = 25.0
    elif members >= 300000:
        members_score = 22.0
    elif members >= 120000:
        members_score = 18.0
    elif members >= 50000:
        members_score = 14.0
    elif members >= 15000:
        members_score = 10.0
    elif members >= 3000:
        members_score = 6.0
    elif members > 0:
        members_score = 3.0
    else:
        members_score = 0.0

    source_score = 20.0 if source == "search" else 14.0
    depth_score = max(0.0, 10.0 - max(0, int(depth)) * 2.5)
    total = int(max(0.0, min(100.0, relevance_score + members_score + source_score + depth_score)))

    return ChannelDiscoveryScore(
        score_total=total,
        breakdown={
            "relevance": relevance_score,
            "members": members_score,
            "source": source_score,
            "depth": depth_score,
        },
        extracted_signals={
            "query_overlap": overlap,
            "query_exact_in_title": bool(exact_query),
            "members_count": members,
            "depth": depth,
            "source": source,
        },
    )


def _tokenize(text: str) -> Set[str]:
    if not text:
        return set()
    return {tok for tok in re.split(r"[^a-zA-Zа-яА-Я0-9_]+", text.lower()) if len(tok) >= 2}


def _overlap_ratio(source: Iterable[str], target: Set[str]) -> float:
    src = list(source)
    if not src or not target:
        return 0.0
    hits = sum(1 for tok in src if tok in target)
    return hits / max(1, len(src))
