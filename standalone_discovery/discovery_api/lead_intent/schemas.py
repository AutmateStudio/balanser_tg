"""Pydantic-схемы для POST /discover-leads."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LeadIntentRequest(BaseModel):
    session_name: Optional[str] = Field(
        default=None,
        description=(
            "Имя Telethon .session. Не указан + async=true + USE_PG_QUEUE=true — "
            "аккаунт подберёт балансировщик."
        ),
    )
    query: str = Field(..., description="Ниша / тема поиска лидов (например «дизайн»)")
    first_pass_limit: int = Field(default=10, ge=1, le=50)
    max_seeds: int = Field(default=25, ge=1, le=60)
    search_pages: int = Field(default=3, ge=1, le=4)
    graph_depth: int = Field(default=1, ge=0, le=2)
    max_graph_seeds: int = Field(default=30, ge=0, le=100)
    min_lead_score: int = Field(default=50, ge=0, le=100)
    posts_limit: int = Field(default=30, ge=5, le=50)
    extra_intents: List[str] = Field(default_factory=list)
    force_refresh_posts: bool = Field(
        default=False,
        description="Игнорировать кэш GetFullChannel / scored_at < 7 дней",
    )


class LeadCandidateItem(BaseModel):
    peer_id: int
    title: str
    username: Optional[str] = None
    participants_count: Optional[int] = None
    source: str = "search_global"
    matched_seed: Optional[str] = None
    lead_score: int = 0
    lead_probability: float = 0.0
    is_job_board: bool = False
    is_community: bool = False
    is_client_base: bool = False
    intent_hits: List[str] = Field(default_factory=list)
    spam_hits: List[str] = Field(default_factory=list)
    graph_seeds: List[str] = Field(default_factory=list)
    broadcast: Optional[bool] = None
    megagroup: Optional[bool] = None
    linked_chat_id: Optional[int] = None
    from_cache: bool = False
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)


class LeadPersistStats(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped_low_score: int = 0
    channel_ids: List[int] = Field(default_factory=list)


class LeadIntentResponse(BaseModel):
    query: str
    seeds: List[str] = Field(default_factory=list)
    total: int = 0
    candidates: List[LeadCandidateItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    persist: Optional[LeadPersistStats] = None
    task_id: Optional[int] = None
    action_id: Optional[str] = None
    async_mode: bool = False
    leased_session_name: Optional[str] = Field(
        default=None,
        description="Сессия, взятая внеочередным lease (/discover-leads/direct)",
    )
    lease_task_id: Optional[int] = Field(
        default=None,
        description="id эфемерной task_queue-записи lease",
    )
    lease_availability_percent: Optional[float] = Field(
        default=None,
        description="Ops-scoped available % выбранного аккаунта на момент lease",
    )
