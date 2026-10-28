"""Скоринг пригодности канала/группы для лидогенерации (0–100)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from discovery_api.config import (
    get_lidgen_dead_days,
    get_lidgen_members_sample_limit,
    get_lidgen_recent_posts_limit,
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


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _weighted_geom(values: List[float], weights: List[float], eps: float = 1e-6) -> float:
    """Взвешенное геометрическое среднее в [0,1]."""
    if not values or not weights or len(values) != len(weights):
        return 0.0
    wsum = sum(weights)
    if wsum <= 0:
        return 0.0
    acc = 0.0
    for v, w in zip(values, weights):
        acc += w * math.log(max(float(v), eps))
    return math.exp(acc / wsum)


def _parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _channel_age_days(created_at: Optional[str], now: datetime) -> Optional[float]:
    dt = _parse_iso_dt(created_at)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _relevance(
    query: str,
    title: str,
    username: Optional[str],
    about: Optional[str],
    extra_seed_tokens: Optional[Set[str]] = None,
) -> Tuple[float, Dict[str, Any]]:
    q = (query or "").strip()
    title_l = (title or "").strip().lower()
    about_l = (about or "").strip().lower()
    name_tokens = _tokenize(title_l) | _tokenize((username or "").strip().lower()) | _tokenize(about_l)
    if extra_seed_tokens:
        query_tokens = list(_tokenize(q)) + list(extra_seed_tokens)
    else:
        query_tokens = list(_tokenize(q))
    overlap = _overlap_ratio(query_tokens, name_tokens) if query_tokens else 0.0
    exact_title = 1.0 if q and q.lower() in title_l else 0.0
    exact_about = 1.0 if q and q.lower() in about_l else 0.0
    if not q:
        rel = 1.0
    else:
        rel = _clamp01(overlap * 0.75 + exact_title * 0.15 + exact_about * 0.1)
    return rel, {
        "query_overlap": overlap,
        "query_exact_in_title": bool(exact_title),
        "query_exact_in_about": bool(exact_about),
    }


def _piece_cadence(posts_30d: float) -> float:
    """Оптимум ~5–60 постов за 30 дней."""
    if posts_30d < 1:
        return _clamp01(posts_30d)
    if posts_30d <= 5:
        return _clamp01(0.5 + 0.1 * posts_30d)
    if posts_30d <= 60:
        return 1.0
    if posts_30d <= 200:
        return _clamp01(1.0 - (posts_30d - 60) / 280.0)
    return _clamp01(0.35)


def _piece_engagement_rate(ratio: float) -> float:
    """ratio = avg_views / participants (доля 0..1)."""
    pct = ratio * 100.0
    if pct <= 0:
        return 0.15
    if pct < 0.5:
        return _clamp01(pct / 0.5 * 0.35)
    if pct <= 15.0:
        return _clamp01(0.35 + (pct - 0.5) / 14.5 * 0.65)
    if pct <= 40.0:
        return _clamp01(1.0 - (pct - 15.0) / 25.0 * 0.45)
    return _clamp01(0.25)


def _piece_online_ratio(pct: float) -> float:
    """pct = online/participants * 100."""
    if pct <= 0:
        return 0.2
    if pct < 0.15:
        return _clamp01(pct / 0.15 * 0.4)
    if pct <= 3.0:
        return _clamp01(0.4 + (pct - 0.15) / 2.85 * 0.6)
    if pct <= 12.0:
        return _clamp01(1.0 - (pct - 3.0) / 9.0 * 0.55)
    return _clamp01(0.2)


def _cv(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    if m <= 0:
        return None
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    std = math.sqrt(max(0.0, var))
    return std / m


def _cv_score(cv_val: Optional[float]) -> float:
    if cv_val is None:
        return 0.5
    if cv_val < 0.08:
        return 0.25
    if cv_val < 0.15:
        return 0.55
    if cv_val <= 2.5:
        return 1.0
    if cv_val <= 4.0:
        return _clamp01(1.0 - (cv_val - 2.5) / 1.5 * 0.45)
    return 0.35


@dataclass(frozen=True)
class LidgenScore:
    score_total: int
    breakdown: Dict[str, float]
    extracted_signals: Dict[str, Any]
    hard_flags: Dict[str, bool] = field(default_factory=dict)


def score_channel_for_lidgen(
    *,
    signals: Dict[str, Any],
    query: str,
    depth: int = 0,
    source: str = "search",
) -> LidgenScore:
    """Оценка broadcast-канала или megagroup по словарю сигналов от `collect_lidgen_signals`."""
    return _compute_lidgen_score(signals, query=query, depth=depth, source=source, extra_seed_tokens=None)


def score_group_for_lidgen(
    *,
    signals: Dict[str, Any],
    query: str,
    matched_seed: Optional[str],
    depth: int = 0,
    source: str = "global_search",
) -> LidgenScore:
    """Как `score_channel_for_lidgen`, но в релевантность добавляются токены seed."""
    extra = _tokenize(matched_seed or "") if matched_seed else set()
    return _compute_lidgen_score(signals, query=query, depth=depth, source=source, extra_seed_tokens=extra or None)


def _compute_lidgen_score(
    signals: Dict[str, Any],
    *,
    query: str,
    depth: int,
    source: str,
    extra_seed_tokens: Optional[Set[str]],
) -> LidgenScore:
    dead_days = get_lidgen_dead_days()
    now = datetime.now(timezone.utc)

    meta = signals.get("meta") or {}
    title = str(signals.get("title") or "")
    username = signals.get("username")
    about = signals.get("about")
    participants = max(0, int(signals.get("participants_count") or 0))
    online = signals.get("online_count")
    online_n = int(online) if online is not None else 0

    scam = bool(meta.get("scam"))
    fake = bool(meta.get("fake"))
    restricted = bool(meta.get("restricted"))
    noforwards = bool(meta.get("noforwards"))
    join_to_send = bool(meta.get("join_to_send"))
    join_request = bool(meta.get("join_request"))
    megagroup = bool(meta.get("megagroup"))
    broadcast = bool(meta.get("broadcast"))
    linked_chat_id = signals.get("linked_chat_id")

    posts: List[Dict[str, Any]] = list(signals.get("posts") or [])
    ms = signals.get("members_sample") or {}
    sampled = int(ms.get("sampled") or 0)
    bots = int(ms.get("bots") or 0)

    hard_flags: Dict[str, bool] = {"scam": scam, "fake": fake, "dead": False, "tiny_audience": False}

    if scam or fake:
        return LidgenScore(
            score_total=0,
            breakdown={"relevance": 0.0, "liveness": 0.0, "audience_quality": 0.0, "reachability": 0.0},
            extracted_signals={"reason": "scam_or_fake"},
            hard_flags=hard_flags,
        )

    rel, rel_sig = _relevance(query, title, username if isinstance(username, str) else None, about if isinstance(about, str) else None, extra_seed_tokens)

    cutoff = now.timestamp() - 30 * 86400
    posts_30d = 0
    last_ts: Optional[float] = None
    views_list: List[float] = []
    reaction_dominance: List[float] = []
    total_reactions = 0
    total_views = 0
    n_views = 0
    replies_vals: List[float] = []

    for p in posts:
        d_raw = p.get("date_ts")
        if d_raw is None:
            continue
        try:
            ts = float(d_raw)
        except (TypeError, ValueError):
            continue
        if last_ts is None or ts > last_ts:
            last_ts = ts
        if ts >= cutoff:
            posts_30d += 1
        v = p.get("views")
        if v is not None:
            try:
                fv = float(v)
                if fv > 0:
                    views_list.append(fv)
                    total_views += fv
                    n_views += 1
            except (TypeError, ValueError):
                pass
        rtot = p.get("reactions_total")
        if rtot is not None:
            try:
                tr = int(rtot)
                total_reactions += max(0, tr)
                dom = float(p.get("reaction_dominance") or 0.0)
                reaction_dominance.append(dom)
            except (TypeError, ValueError):
                pass
        rep = p.get("replies")
        if rep is not None:
            try:
                replies_vals.append(float(rep))
            except (TypeError, ValueError):
                pass

    days_since_last: Optional[float] = None
    if last_ts is not None:
        days_since_last = max(0.0, (now.timestamp() - last_ts) / 86400.0)

    if days_since_last is None or days_since_last > float(dead_days):
        hard_flags["dead"] = bool(days_since_last is None or days_since_last > float(dead_days))

    age_days = _channel_age_days(meta.get("created_at") if isinstance(meta.get("created_at"), str) else None, now)
    if participants < 30 and age_days is not None and age_days > 30:
        hard_flags["tiny_audience"] = True

    freshness = 1.0 if days_since_last is None else _clamp01(1.0 - min(days_since_last, 60.0) / 60.0)
    cadence = _piece_cadence(float(posts_30d))

    avg_views = total_views / max(1, n_views)
    er_ratio = (avg_views / participants) if participants > 0 and n_views > 0 else 0.0
    if broadcast and participants > 0 and n_views > 0:
        engagement = _piece_engagement_rate(er_ratio)
    elif megagroup and (replies_vals or views_list):
        rf = 0.0
        if replies_vals:
            rf += sum(replies_vals) / len(replies_vals) / 50.0
        if views_list:
            rf += min(1.0, avg_views / max(1.0, float(participants)) * 80.0)
        engagement = _clamp01(min(1.0, rf))
    else:
        engagement = 0.35

    rpv = (total_reactions / total_views) if total_views > 0 else 0.0
    reactions_score = _clamp01(min(1.0, rpv / 0.015))

    if linked_chat_id and replies_vals:
        comments_score = _clamp01(min(1.0, (sum(replies_vals) / len(replies_vals)) / 25.0))
    elif linked_chat_id:
        comments_score = 0.45
    else:
        comments_score = 1.0

    liveness_vals = [freshness, cadence, engagement, reactions_score, comments_score]
    liveness_w = [0.30, 0.20, 0.30, 0.10, 0.10]
    liveness = _weighted_geom(liveness_vals, liveness_w)

    online_pct = (online_n / participants * 100.0) if participants > 0 and online_n > 0 else 0.0
    online_score = _piece_online_ratio(online_pct)

    views_for_cv = views_list[-20:] if len(views_list) > 20 else views_list
    cv_val = _cv(views_for_cv)
    cv_s = _cv_score(cv_val)

    dom_frac = 0.0
    if reaction_dominance:
        dom_frac = sum(1 for d in reaction_dominance if d >= 0.7) / len(reaction_dominance)
    react_pattern = 1.0 if dom_frac < 0.5 else _clamp01(1.0 - (dom_frac - 0.5) / 0.5 * 0.75)

    if megagroup and sampled > 0:
        br = bots / sampled
        if br <= 0.02:
            bot_score = 1.0
        elif br >= 0.10:
            bot_score = 0.1
        else:
            bot_score = 1.0 - (br - 0.02) / 0.08 * 0.9
        bot_score = _clamp01(bot_score)
    else:
        bot_score = 0.75

    audience = (
        online_score * 0.30 + cv_s * 0.20 + react_pattern * 0.15 + bot_score * 0.35
    )

    reach = 0.0
    if linked_chat_id:
        reach += 0.40
    if megagroup:
        reach += 0.30
    about_s = (about or "") if isinstance(about, str) else ""
    if re.search(r"@[\w\d_]{3,}", about_s) or re.search(r"https?://", about_s, re.I):
        reach += 0.10
    if noforwards:
        reach -= 0.20
    if join_to_send:
        reach -= 0.10
    if join_request:
        reach -= 0.20
    sm = signals.get("slowmode_seconds") or 0
    try:
        if int(sm) > 0:
            reach -= 0.05
    except (TypeError, ValueError):
        pass
    if restricted:
        reach -= 0.50
    reach = _clamp01(reach)

    core = _weighted_geom([liveness, audience, reach], [1.0, 1.0, 1.0])
    depth_factor = max(0.55, 0.92**int(depth))
    source_factor = 1.0 if source in ("search", "contacts_search", "global_search") else 0.96

    size_boost = 1.0 + 0.06 * math.log10(participants + 10.0)
    size_boost = min(1.25, max(1.0, size_boost))

    base = 100.0 * (rel**0.5) * core * depth_factor * source_factor * size_boost
    total_f = base

    if hard_flags["dead"]:
        total_f *= 0.1
    if hard_flags["tiny_audience"]:
        total_f *= 0.3

    total = int(round(max(0.0, min(100.0, total_f))))

    breakdown = {
        "relevance": rel,
        "liveness": liveness,
        "audience_quality": audience,
        "reachability": reach,
        "geom_core": core,
        "depth_factor": depth_factor,
        "source_factor": source_factor,
        "size_boost": size_boost,
    }

    extracted: Dict[str, Any] = {
        **rel_sig,
        "freshness": freshness,
        "cadence_posts_30d": posts_30d,
        "engagement_proxy": engagement,
        "reactions_per_view": rpv,
        "comments_proxy": comments_score if linked_chat_id else None,
        "online_ratio_pct": online_pct,
        "views_cv": cv_val,
        "reaction_dominant_posts_frac": dom_frac,
        "bots_ratio_sample": (bots / sampled) if sampled else None,
        "members_count": participants,
        "posts_scanned": len(posts),
        "depth": depth,
        "source": source,
        "recent_posts_limit": get_lidgen_recent_posts_limit(),
        "members_sample_limit": get_lidgen_members_sample_limit(),
        "dead_days_threshold": dead_days,
        "days_since_last_post": days_since_last,
    }
    if signals.get("collector_errors"):
        extracted["collector_errors"] = signals["collector_errors"]

    return LidgenScore(
        score_total=total,
        breakdown=breakdown,
        extracted_signals=extracted,
        hard_flags=hard_flags,
    )
