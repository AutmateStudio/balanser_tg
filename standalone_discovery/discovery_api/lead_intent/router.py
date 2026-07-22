"""HTTP: POST /discovery-api/discover-leads (+ /direct без очереди)."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app_balance.queue.account_lease import (
    NoAccountAvailableError,
    acquire_best_account_lease,
)
from discovery_api.config import get_use_pg_queue
from discovery_api.lead_intent.pipeline import run_lead_intent_on_client
from discovery_api.lead_intent.producer import (
    TELEGRAM_DISCOVER_LEADS,
    enqueue_telegram_discover_leads,
)
from discovery_api.lead_intent.schemas import (
    LeadCandidateItem,
    LeadIntentRequest,
    LeadIntentResponse,
    LeadPersistStats,
)
from discovery_api.session_registry import get_or_create_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery-api", tags=["discovery-api-lead-intent"])

CREATED_BY_DIRECT = "discovery_api:discover-leads-direct"


def _to_response(
    result,
    *,
    task_id: Optional[int] = None,
    action_id: Optional[str] = None,
    async_mode: bool = False,
    leased_session_name: Optional[str] = None,
    lease_task_id: Optional[int] = None,
    lease_availability_percent: Optional[float] = None,
) -> LeadIntentResponse:
    persist = None
    if result.persist is not None:
        persist = LeadPersistStats(
            inserted=result.persist.inserted,
            updated=result.persist.updated,
            skipped_low_score=result.persist.skipped_low_score,
            channel_ids=list(result.persist.channel_ids),
        )
    return LeadIntentResponse(
        query=result.query,
        seeds=list(result.seeds),
        total=result.total,
        candidates=[
            LeadCandidateItem(
                peer_id=c.peer_id,
                title=c.title,
                username=c.username,
                participants_count=c.participants_count,
                source=c.source,
                matched_seed=c.matched_seed,
                lead_score=c.lead_score,
                lead_probability=c.lead_probability,
                is_job_board=c.is_job_board,
                is_community=c.is_community,
                is_client_base=c.is_client_base,
                intent_hits=list(c.intent_hits),
                spam_hits=list(c.spam_hits),
                graph_seeds=list(c.graph_seeds),
                broadcast=c.is_broadcast,
                megagroup=c.is_megagroup,
                linked_chat_id=c.linked_chat_id,
                from_cache=c.from_cache,
                score_breakdown=dict(c.score_breakdown),
            )
            for c in result.candidates
        ],
        errors=list(result.errors),
        persist=persist,
        task_id=task_id,
        action_id=action_id,
        async_mode=async_mode,
        leased_session_name=leased_session_name,
        lease_task_id=lease_task_id,
        lease_availability_percent=lease_availability_percent,
    )


@router.post(
    "/discover-leads",
    response_model=LeadIntentResponse,
    summary="Intent-based поиск лидов (изолированный модуль)",
)
async def discover_leads(
    body: LeadIntentRequest = Body(...),
    async_mode: bool = Query(
        True,
        alias="async",
        description="true — PG-очередь telegram_discover_leads; false — sync на session_name",
    ),
) -> LeadIntentResponse:
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query обязателен")

    use_queue = get_use_pg_queue() and async_mode
    action_id = str(uuid.uuid4())

    if use_queue:
        enq = await enqueue_telegram_discover_leads(
            session_name=body.session_name,
            query=query,
            first_pass_limit=body.first_pass_limit,
            max_seeds=body.max_seeds,
            search_pages=body.search_pages,
            graph_depth=body.graph_depth,
            max_graph_seeds=body.max_graph_seeds,
            min_lead_score=body.min_lead_score,
            posts_limit=body.posts_limit,
            extra_intents=list(body.extra_intents or []),
            force_refresh_posts=body.force_refresh_posts,
            action_id=action_id,
        )
        if enq.task_id is None and (body.session_name or "").strip():
            raise HTTPException(
                status_code=404,
                detail="аккаунт с указанным session_name не найден в PG",
            )
        return LeadIntentResponse(
            query=query,
            seeds=[],
            total=0,
            candidates=[],
            errors=[],
            persist=None,
            task_id=enq.task_id,
            action_id=enq.action_id,
            async_mode=True,
        )

    session = (body.session_name or "").strip()
    if not session:
        raise HTTPException(
            status_code=400,
            detail="session_name обязателен для sync-режима (async=false)",
        )

    client = await get_or_create_client(session)
    try:
        result = await run_lead_intent_on_client(
            client,
            query,
            first_pass_limit=body.first_pass_limit,
            max_seeds=body.max_seeds,
            search_pages=body.search_pages,
            graph_depth=body.graph_depth,
            max_graph_seeds=body.max_graph_seeds,
            min_lead_score=body.min_lead_score,
            posts_limit=body.posts_limit,
            extra_intents=list(body.extra_intents or []),
            force_refresh_posts=body.force_refresh_posts,
            persist=True,
        )
    except Exception as e:
        log.exception("discover-leads sync failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return _to_response(result, action_id=action_id, async_mode=False)


@router.post(
    "/discover-leads/direct",
    response_model=LeadIntentResponse,
    summary="Intent-поиск без очереди: lease самого живого аккаунта",
)
async def discover_leads_direct(
    body: LeadIntentRequest = Body(...),
) -> LeadIntentResponse:
    """Sync: резервирует аккаунт с max ops-scoped available %, гоняет pipeline, release."""
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query обязателен")

    if (body.session_name or "").strip():
        log.warning(
            "discover-leads/direct: session_name=%r игнорируется (auto lease)",
            body.session_name,
        )

    action_id = str(uuid.uuid4())
    payload = {
        "query": query,
        "first_pass_limit": body.first_pass_limit,
        "max_seeds": body.max_seeds,
        "search_pages": body.search_pages,
        "graph_depth": body.graph_depth,
        "max_graph_seeds": body.max_graph_seeds,
        "min_lead_score": body.min_lead_score,
        "posts_limit": body.posts_limit,
        "extra_intents": list(body.extra_intents or []),
        "force_refresh_posts": body.force_refresh_posts,
        "action_id": action_id,
    }

    try:
        async with acquire_best_account_lease(
            TELEGRAM_DISCOVER_LEADS,
            created_by=CREATED_BY_DIRECT,
            payload=payload,
        ) as lease:
            client = await get_or_create_client(lease.session_name)
            try:
                result = await run_lead_intent_on_client(
                    client,
                    query,
                    first_pass_limit=body.first_pass_limit,
                    max_seeds=body.max_seeds,
                    search_pages=body.search_pages,
                    graph_depth=body.graph_depth,
                    max_graph_seeds=body.max_graph_seeds,
                    min_lead_score=body.min_lead_score,
                    posts_limit=body.posts_limit,
                    extra_intents=list(body.extra_intents or []),
                    force_refresh_posts=body.force_refresh_posts,
                    persist=True,
                )
            except Exception as e:
                log.exception(
                    "discover-leads/direct pipeline failed session=%s",
                    lease.session_name,
                )
                raise HTTPException(status_code=500, detail=str(e)) from e

            return _to_response(
                result,
                action_id=action_id,
                async_mode=False,
                leased_session_name=lease.session_name,
                lease_task_id=lease.task_id,
                lease_availability_percent=lease.availability_percent,
            )
    except NoAccountAvailableError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": e.code, "message": str(e)},
        ) from e
