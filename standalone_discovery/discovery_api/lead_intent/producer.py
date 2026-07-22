"""Продюсер задачи telegram_discover_leads (не трогает telegram_discover)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from app_balance.queue.accounts import AccountsRepo
from app_balance.queue.task_queue import EnqueueInput, TaskQueueRepo

log = logging.getLogger(__name__)

TELEGRAM_DISCOVER_LEADS = "telegram_discover_leads"
CREATED_BY_DISCOVER_LEADS = "discovery_api:discover-leads"


@dataclass(frozen=True, slots=True)
class EnqueueTelegramDiscoverLeadsResult:
    task_id: int | None
    action_id: str


def _task_id_from_enqueue(result) -> int | None:
    if result.created and result.task_id is not None:
        return int(result.task_id)
    if result.existing_task_id is not None:
        return int(result.existing_task_id)
    return None


def _dedup_key(
    session_name: str | None,
    query: str,
    *,
    first_pass_limit: int,
    max_seeds: int,
    search_pages: int,
    graph_depth: int,
    min_lead_score: int,
) -> str:
    stripped = (session_name or "").strip()
    if stripped:
        from app_balance.queue.accounts_sync import normalize_session_name

        session = normalize_session_name(stripped)
    else:
        session = "auto"
    normalized_query = (query or "").strip().lower()
    return (
        f"{TELEGRAM_DISCOVER_LEADS}:{session}:{normalized_query}:"
        f"{first_pass_limit}:{max_seeds}:{search_pages}:{graph_depth}:{min_lead_score}"
    )


async def enqueue_telegram_discover_leads(
    *,
    session_name: str | None = None,
    query: str,
    first_pass_limit: int,
    max_seeds: int,
    search_pages: int,
    graph_depth: int,
    max_graph_seeds: int,
    min_lead_score: int,
    posts_limit: int,
    extra_intents: Optional[List[str]] = None,
    force_refresh_posts: bool = False,
    action_id: str,
) -> EnqueueTelegramDiscoverLeadsResult:
    trimmed_query = (query or "").strip()
    if not trimmed_query:
        return EnqueueTelegramDiscoverLeadsResult(task_id=None, action_id=action_id)

    session_stripped = (session_name or "").strip()
    account_id: int | None = None
    normalized_session: str | None = None

    if session_stripped:
        from app_balance.queue.accounts_sync import normalize_session_name

        accounts = AccountsRepo()
        normalized_session = normalize_session_name(session_stripped)
        account_id = await accounts.get_id_by_session_name(session_stripped)
        if account_id is None:
            log.warning(
                "enqueue_telegram_discover_leads: аккаунт не в PG session=%r",
                session_stripped,
            )
            return EnqueueTelegramDiscoverLeadsResult(task_id=None, action_id=action_id)

    payload: dict[str, Any] = {
        "query": trimmed_query,
        "first_pass_limit": int(first_pass_limit),
        "max_seeds": int(max_seeds),
        "search_pages": int(search_pages),
        "graph_depth": int(graph_depth),
        "max_graph_seeds": int(max_graph_seeds),
        "min_lead_score": int(min_lead_score),
        "posts_limit": int(posts_limit),
        "extra_intents": list(extra_intents or []),
        "force_refresh_posts": bool(force_refresh_posts),
        "action_id": action_id,
    }
    if normalized_session:
        payload["session_name"] = normalized_session

    repo = TaskQueueRepo()
    result = await repo.enqueue(
        EnqueueInput(
            task_type_code=TELEGRAM_DISCOVER_LEADS,
            payload=payload,
            dedup_key=_dedup_key(
                session_stripped or None,
                trimmed_query,
                first_pass_limit=first_pass_limit,
                max_seeds=max_seeds,
                search_pages=search_pages,
                graph_depth=graph_depth,
                min_lead_score=min_lead_score,
            ),
            created_by=CREATED_BY_DISCOVER_LEADS,
            account_id=account_id,
        )
    )
    task_id = _task_id_from_enqueue(result)
    return EnqueueTelegramDiscoverLeadsResult(task_id=task_id, action_id=action_id)
