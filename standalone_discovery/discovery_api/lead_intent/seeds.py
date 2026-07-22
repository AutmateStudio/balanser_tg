"""Генератор intent-сидов для SearchGlobal / contacts.Search."""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from discovery_api.lead_intent.keywords import (
    DESIGN_EXTRA_SEEDS,
    DESIGN_NICHE_TOKENS,
    GROUP_SUFFIXES,
    INTENT_TEMPLATES,
    MARKER_SEEDS,
)


def _is_design_niche(niche: str) -> bool:
    tokens = {t for t in niche.lower().replace("/", " ").split() if t}
    tokens.add(niche.strip().lower())
    return bool(tokens & DESIGN_NICHE_TOKENS)


def generate_intent_seeds(
    query: str,
    *,
    max_seeds: int = 25,
    extra_intents: Optional[Sequence[str]] = None,
) -> List[str]:
    """Строит список сидов: сначала intent-фразы, затем group-suffix.

    Приоритет: extra_intents → INTENT_TEMPLATES → MARKER_SEEDS →
    design extras (если ниша design-like) → group suffixes.
    """
    niche = (query or "").strip()
    if not niche:
        return []

    limit = max(1, min(int(max_seeds), 60))
    ordered: List[str] = []

    def _push(items: Iterable[str]) -> None:
        for raw in items:
            s = (raw or "").strip()
            if not s:
                continue
            # дедуп без учёта регистра, сохраняя первый регистр
            key = s.lower()
            if any(x.lower() == key for x in ordered):
                continue
            ordered.append(s)
            if len(ordered) >= limit:
                return

    extras = [str(x).strip() for x in (extra_intents or []) if str(x).strip()]
    _push(extras)
    if len(ordered) >= limit:
        return ordered[:limit]

    _push(tpl.format(q=niche) for tpl in INTENT_TEMPLATES)
    if len(ordered) >= limit:
        return ordered[:limit]

    _push(MARKER_SEEDS)
    if len(ordered) >= limit:
        return ordered[:limit]

    if _is_design_niche(niche):
        _push(DESIGN_EXTRA_SEEDS)
        if len(ordered) >= limit:
            return ordered[:limit]

    _push(f"{niche} {suffix}" for suffix in GROUP_SUFFIXES)
    return ordered[:limit]


def is_group_pass_seed(seed: str, niche: str) -> bool:
    """Сиды вида «{niche} чат|группа|…» — для contacts.Search."""
    s = (seed or "").strip().lower()
    n = (niche or "").strip().lower()
    if not s or not n:
        return False
    for suffix in GROUP_SUFFIXES:
        if s == f"{n} {suffix}".lower():
            return True
    return False
