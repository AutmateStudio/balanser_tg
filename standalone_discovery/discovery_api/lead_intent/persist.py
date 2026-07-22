"""Persist кандидатов lead_intent в source_channels.metadata."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app_balance.queue.discover_persist import get_telegram_platform_id
from app_balance.queue.source_channels import SourceChannelsRepo
from discovery_api.lead_intent.keywords import PIPELINE_VERSION

if TYPE_CHECKING:
    from discovery_api.lead_intent.pipeline import LeadCandidate


def get_min_lead_score(default: int = 50) -> int:
    raw = os.getenv("LEAD_INTENT_MIN_SCORE", "").strip()
    if not raw:
        return default
    try:
        return max(0, min(int(raw), 100))
    except ValueError:
        return default


@dataclass
class LeadPersistStats:
    inserted: int = 0
    updated: int = 0
    skipped_low_score: int = 0
    channel_ids: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_low_score": self.skipped_low_score,
            "channel_ids": list(self.channel_ids),
        }


def should_persist_lead(
    candidate: "LeadCandidate",
    *,
    min_score: Optional[int] = None,
) -> bool:
    threshold = get_min_lead_score() if min_score is None else int(min_score)
    if candidate.lead_score >= threshold:
        return True
    # broadcast / client_base с intent-hits — сохраняем даже чуть ниже порога,
    # если есть явные интенты в постах/комментариях
    if candidate.intent_hits and (candidate.is_client_base or candidate.is_broadcast):
        return candidate.lead_score >= max(0, threshold - 15)
    return False


def build_lead_intent_metadata(candidate: "LeadCandidate") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    lead_block = {
        "lead_score": int(candidate.lead_score),
        "lead_probability": float(candidate.lead_probability),
        "is_job_board": bool(candidate.is_job_board),
        "is_community": bool(candidate.is_community),
        "is_client_base": bool(candidate.is_client_base),
        "intent_hits": list(candidate.intent_hits or []),
        "spam_hits": list(candidate.spam_hits or []),
        "graph_seeds": list(candidate.graph_seeds or []),
        "matched_seed": candidate.matched_seed,
        "source": candidate.source,
        "scored_at": now,
        "pipeline": PIPELINE_VERSION,
        "breakdown": dict(candidate.score_breakdown or {}),
    }
    username = (candidate.username or "").strip() or None
    return {
        "name": candidate.title,
        "username": username,
        "participants_count": candidate.participants_count,
        "entity_kind": "channel" if candidate.is_broadcast else "group",
        "source": candidate.source,
        "linked_chat_id": candidate.linked_chat_id,
        "lead_intent": lead_block,
        # дублируем score на верхний уровень для удобства n8n (не конфликтует с lidgen)
        "lead_score": int(candidate.lead_score),
    }


def build_upsert_fields(candidate: "LeadCandidate") -> Dict[str, Any]:
    username = (candidate.username or "").strip() or None
    external_url = f"https://t.me/{username}" if username else None
    return {
        "external_channel_id": str(int(candidate.peer_id)),
        "name": (candidate.title or "").strip() or None,
        "description": None,
        "external_url": external_url,
        "metadata": build_lead_intent_metadata(candidate),
    }


async def persist_lead_candidates(
    candidates: List["LeadCandidate"],
    *,
    min_score: Optional[int] = None,
    channels_repo: Optional[SourceChannelsRepo] = None,
) -> LeadPersistStats:
    repo = channels_repo or SourceChannelsRepo()
    platform_id = await get_telegram_platform_id()
    stats = LeadPersistStats()
    threshold = get_min_lead_score() if min_score is None else int(min_score)

    for cand in candidates:
        if not should_persist_lead(cand, min_score=threshold):
            stats.skipped_low_score += 1
            continue
        fields = build_upsert_fields(cand)
        result = await repo.upsert_discovered(
            platform_id=platform_id,
            external_channel_id=fields["external_channel_id"],
            name=fields.get("name"),
            description=fields.get("description"),
            external_url=fields.get("external_url"),
            metadata=fields.get("metadata") or {},
        )
        if result is None:
            continue
        stats.channel_ids.append(result.channel_id)
        if result.inserted:
            stats.inserted += 1
        else:
            stats.updated += 1
    return stats
