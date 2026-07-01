"""HTTP-маршруты для запуска и остановки Telegram-парсера (SessionClump)."""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl, model_validator

from discovery_api.account_registry import (
    delete_account_full,
    list_all_accounts_merged,
    normalize_session_name,
    session_file_exists,
)
from discovery_api.account_store import set_admin_blocked, update_account_fields, upsert_account
from discovery_api.action_queue import (
    enqueue_action,
    get_action,
    list_actions,
    register_action_handler,
    start_action_worker,
    update_action_progress,
)
from discovery_api.config import (
    get_add_channels_per_hour,
    get_max_channels_per_session,
    get_rebalance_cooldown_hours,
    get_rebalance_enabled,
    get_rebalance_high_watermark_ratio,
    get_rebalance_idle_end_hour,
    get_rebalance_idle_start_hour,
    get_rebalance_low_watermark_ratio,
    get_rebalance_max_moves_per_tick,
    get_rebalance_min_gap_channels,
    get_session_auto_migrate,
    get_session_flood_migrate_threshold_seconds,
    get_session_health_check_interval,
    get_session_max_reconnects,
    get_session_reconnect_backoff_base,
    get_session_reconnect_backoff_max,
    get_session_resolve_min_interval,
    get_use_pg_queue,
)
from discovery_api.parser_functions import get_runtime_queue_size, get_runtime_stats
from discovery_api.queue.producer import (
    enqueue_parser_add_channels,
    enqueue_parser_remove_channels,
)
from discovery_api.queue.metrics import MetricsResponse, get_queue_metrics
from discovery_api.queue.task_types import (
    TaskTypeDetailResponse,
    TaskTypeListItemResponse,
    TaskTypePatchRequest,
    get_task_type,
    list_task_types,
    patch_task_type,
)
from discovery_api.queue.account_queue_overlay import (
    fetch_pg_queue_states,
    overlay_account_rows,
    overlay_queue_state,
)
from discovery_api.queue.status import get_task_snapshot
from discovery_api.parser_store import (
    clump_to_record,
    delete_job as persist_delete_job,
    is_persistence_enabled,
    load_persisted_jobs,
    normalize_persisted_record,
    upsert_job as persist_upsert_job,
)
from discovery_api.session_registry import (
    ChannelQuotaExceeded,
    SessionClump,
    get_or_create_clump,
    release_client,
    remove_clump,
)

log = logging.getLogger(__name__)

parser_router = APIRouter(prefix="/discovery-api/parser", tags=["telegram-parser"])


class ParserStartRequest(BaseModel):
    session_name: Optional[str] = Field(
        None,
        description="Один аккаунт Telethon (legacy); альтернатива session_name_list",
    )
    session_name_list: Optional[list[str]] = Field(
        None,
        min_length=1,
        description="Пул аккаунтов для clump (шардирование каналов)",
    )
    clump_name: Optional[str] = Field(
        None, description="Имя clump для логов (не id в URL)"
    )
    channel_list: list[str] = Field(
        ..., min_length=1, description="@username каналов/чатов или числовые id"
    )
    webhook_url: HttpUrl = Field(..., description="URL для JSON POST при новом сообщении")

    @model_validator(mode="after")
    def _session_mode(self) -> ParserStartRequest:
        has_single = bool((self.session_name or "").strip())
        has_list = bool(self.session_name_list)
        if has_single == has_list:
            raise ValueError(
                "Укажите ровно одно: session_name или непустой session_name_list"
            )
        return self

    def resolved_session_names(self) -> list[str]:
        if self.session_name_list:
            return list(self.session_name_list)
        assert self.session_name is not None
        return [self.session_name]


class ParserStartResponse(BaseModel):
    parser_id: str
    assignments: dict[str, str] = Field(default_factory=dict)
    detail: str = "Clump запущен, слушатели активны"


class ParserStopResponse(BaseModel):
    parser_id: str
    detail: str


class ParserStatusItem(BaseModel):
    parser_id: str
    clump_name: Optional[str] = None
    session_name: Optional[str] = None
    session_name_list: list[str] = Field(default_factory=list)
    webhook_url: str
    channel_list: list[str]
    assignments: dict[str, str] = Field(default_factory=dict)
    per_session: list[dict[str, Any]] = Field(default_factory=list)
    running: bool
    finished: bool
    cancelled: bool
    error: Optional[str] = None
    started_at: float
    queue_size: int = 0
    stats: dict[str, int] = Field(default_factory=dict)
    health_summary: dict[str, Any] = Field(default_factory=dict)


class ChannelsBody(BaseModel):
    channel_list: list[str] = Field(
        ..., min_length=1, description="@username, t.me/... или числовые id"
    )


class ChannelsListResponse(BaseModel):
    parser_id: str
    channel_list: list[str]
    allowed_chat_ids: list[int]
    by_session: dict[str, list[str]] = Field(default_factory=dict)


class ClumpConfigUpdate(BaseModel):
    max_channels_per_session: Optional[int] = Field(default=None, ge=1)
    max_reconnects: Optional[int] = Field(default=None, ge=1)
    reconnect_backoff_base: Optional[float] = Field(default=None, gt=0)
    reconnect_backoff_max: Optional[float] = Field(default=None, ge=1)
    flood_migrate_threshold_seconds: Optional[int] = Field(default=None, ge=1)
    resolve_min_interval: Optional[float] = Field(default=None, ge=0)
    auto_migrate: Optional[bool] = Field(default=None)
    add_channels_per_hour: Optional[int] = Field(default=None, ge=0)
    rebalance_enabled: Optional[bool] = Field(default=None)
    rebalance_idle_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    rebalance_idle_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    rebalance_high_watermark_ratio: Optional[float] = Field(default=None, gt=0, le=1)
    rebalance_low_watermark_ratio: Optional[float] = Field(default=None, ge=0, lt=1)
    rebalance_min_gap_channels: Optional[int] = Field(default=None, ge=1)
    rebalance_max_moves_per_tick: Optional[int] = Field(default=None, ge=1)
    rebalance_cooldown_hours: Optional[float] = Field(default=None, ge=0)


class ClumpConfigResponse(BaseModel):
    parser_id: str
    config: dict[str, Any]


class AccountQueueOverlayFields(BaseModel):
    """PG cooldown + available_at для дашборда (merge с runtime flood)."""

    queue_status: Optional[str] = None
    cooldown_until: Optional[str] = None
    cooldown_remaining_seconds: Optional[int] = None
    available_at: Optional[str] = None
    available_in_seconds: Optional[int] = None
    flood_until: Optional[float] = None
    current_task_id: Optional[int] = None
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None
    is_enabled: Optional[bool] = None


class AccountSummary(AccountQueueOverlayFields):
    parser_id: str
    session_name: str
    display_name: str
    clump_name: Optional[str] = None
    status: str
    banned: bool = False
    ban_reason: Optional[str] = None
    flood_remaining_seconds: Optional[int] = None
    connected: bool = False
    running: bool = False
    channel_count: int = 0
    max_channels_per_session: int = 0


class AccountListResponse(BaseModel):
    total: int
    accounts: list[AccountSummary] = Field(default_factory=list)


class AccountDetail(AccountQueueOverlayFields):
    parser_id: str
    session_name: str
    display_name: str
    description: str = ""
    clump_name: Optional[str] = None
    running: bool = False
    channel_count: int = 0
    limits: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    flood_remaining_seconds: Optional[int] = None


class AccountChannelsResponse(BaseModel):
    parser_id: str
    session_name: str
    channel_count: int = 0
    channels: list[str] = Field(default_factory=list)


class AccountMetaUpdate(BaseModel):
    parser_id: Optional[str] = Field(default=None, min_length=1)
    session_name: str = Field(..., min_length=1)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    max_channels: Optional[int] = Field(default=None, ge=1)


class AccountFullSummary(AccountQueueOverlayFields):
    session_name: str
    display_name: str
    description: str = ""
    max_channels: Optional[int] = None
    effective_max_channels: int = 0
    limit_source: str = "clump"
    admin_blocked: bool = False
    block_reason: Optional[str] = None
    source: str = "import"
    session_file_exists: bool = False
    in_clump: bool = False
    parser_id: Optional[str] = None
    clump_name: Optional[str] = None
    status: str = "offline"
    banned: bool = False
    ban_reason: Optional[str] = None
    flood_remaining_seconds: Optional[int] = None
    connected: bool = False
    running: bool = False
    channel_count: int = 0


class AccountAllListResponse(BaseModel):
    total: int
    accounts: list[AccountFullSummary] = Field(default_factory=list)
    generated_at: Optional[str] = None


class AccountBlockUpdate(BaseModel):
    blocked: bool
    reason: Optional[str] = Field(default=None, max_length=500)


class AccountUpdateBody(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    max_channels: Optional[int] = Field(default=None, ge=1)


class ActionQueuedResponse(BaseModel):
    action_id: str
    status: str
    parser_id: str
    action_type: str


class ActionItemResponse(BaseModel):
    id: str
    action_type: str
    parser_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    progress: dict[str, int] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ActionListResponse(BaseModel):
    total: int
    actions: list[ActionItemResponse] = Field(default_factory=list)


class TaskQueueItemResponse(BaseModel):
    id: int
    task_type_code: str
    status: str
    attempt_count: int
    postpone_count: int
    last_error: Optional[str] = None
    last_error_code: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    run_after: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error_at: Optional[str] = None


class BalancerSettingsResponse(BaseModel):
    settings: dict[str, Any]
    descriptions: dict[str, str] = Field(default_factory=dict)


class SessionBody(BaseModel):
    session_name: str = Field(
        ..., min_length=1, description="Имя/путь Telethon .session-файла на сервере"
    )


class SessionOpResponse(BaseModel):
    parser_id: str
    session_name_list: list[str] = Field(default_factory=list)
    detail: str


class AddChannelsResponse(BaseModel):
    parser_id: str
    channel_list: list[str]
    added: list[str] = Field(default_factory=list)
    already_present: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    pending: list[str] = Field(
        default_factory=list,
        description=(
            "Каналы, которые не удалось разместить сейчас (квота/нет здоровых "
            "сессий/FloodWait); будут размещены позже HealthMonitor'ом"
        ),
    )
    assignments: dict[str, str] = Field(default_factory=dict)
    action_id: Optional[str] = None
    task_ids: list[int] = Field(default_factory=list)
    async_mode: bool = False


class RemoveChannelsResponse(BaseModel):
    parser_id: str
    channel_list: list[str]
    removed: list[str] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    action_id: Optional[str] = None
    task_ids: list[int] = Field(default_factory=list)
    async_mode: bool = False


@dataclass
class _ClumpJob:
    clump: SessionClump
    parser_id: str
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    cancelled: bool = False
    error: Optional[str] = None


_jobs: dict[str, _ClumpJob] = {}


def _dt_iso(value: datetime | None) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _task_snapshot_to_response(task) -> TaskQueueItemResponse:
    return TaskQueueItemResponse(
        id=task.id,
        task_type_code=task.task_type_code,
        status=task.status,
        attempt_count=task.attempt_count,
        postpone_count=task.postpone_count,
        last_error=task.last_error,
        last_error_code=task.last_error_code,
        payload=dict(task.payload or {}),
        run_after=_dt_iso(task.run_after),
        started_at=_dt_iso(task.started_at),
        finished_at=_dt_iso(task.finished_at),
        last_error_at=_dt_iso(task.last_error_at),
    )


def _env_telegram_configured() -> bool:
    return bool(os.getenv("API_ID", "").strip() and os.getenv("API_HASH", "").strip())


def _persist_clump_state(parser_id: str, clump: SessionClump) -> None:
    persist_upsert_job(clump_to_record(clump, parser_id=parser_id))


def _require_clump_job(parser_id: str) -> _ClumpJob:
    job = _jobs.get(parser_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Парсер (clump) с таким id не найден")
    return job


def _clump_is_running(clump: SessionClump) -> bool:
    return any(pc.is_running() for pc in clump.parser_client_list)


def _status_from_job(parser_id: str, job: _ClumpJob) -> ParserStatusItem:
    info = job.clump.info()
    sessions = list(job.clump.session_name_list)
    return ParserStatusItem(
        parser_id=parser_id,
        clump_name=job.clump.clump_name,
        session_name=sessions[0] if len(sessions) == 1 else None,
        session_name_list=sessions,
        webhook_url=str(job.clump.webhook_url),
        channel_list=list(info.get("channel_list") or []),
        assignments=dict(info.get("assignments") or {}),
        per_session=list(info.get("per_session") or []),
        running=_clump_is_running(job.clump),
        finished=job.finished,
        cancelled=job.cancelled,
        error=job.error,
        started_at=job.started_at,
        queue_size=get_runtime_queue_size(),
        stats=get_runtime_stats(),
        health_summary=dict(info.get("health_summary") or {}),
    )


@parser_router.post("/start", response_model=ParserStartResponse)
async def parser_start(body: ParserStartRequest) -> ParserStartResponse:
    if not _env_telegram_configured():
        raise HTTPException(
            status_code=500,
            detail="Не заданы переменные окружения API_ID и/или API_HASH",
        )

    parser_id = uuid.uuid4().hex
    webhook_str = str(body.webhook_url)
    session_names = body.resolved_session_names()

    try:
        clump = await get_or_create_clump(
            parser_id,
            session_names,
            webhook_str,
            clump_name=body.clump_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    assignments: dict[str, str] = {}
    errors: list[str] = []

    for raw in body.channel_list:
        try:
            result = await clump.add_channel(raw)
        except ChannelQuotaExceeded as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        err = result.get("error")
        if err:
            errors.append(f"{raw}: {err}")
            continue
        sn = result.get("session_name")
        if sn:
            assignments[str(raw)] = str(sn)

    if errors and not assignments:
        await remove_clump(parser_id)
        _jobs.pop(parser_id, None)
        raise HTTPException(
            status_code=400,
            detail="Не удалось добавить ни одного канала: " + "; ".join(errors[:5]),
        )

    try:
        await clump.start()
    except Exception as e:
        await remove_clump(parser_id)
        _jobs.pop(parser_id, None)
        log.exception("Ошибка запуска clump %s", parser_id)
        raise HTTPException(status_code=500, detail=f"Ошибка запуска слушателей: {e}") from e

    _jobs[parser_id] = _ClumpJob(clump=clump, parser_id=parser_id)
    _persist_clump_state(parser_id, clump)

    detail = "Clump запущен, слушатели активны"
    if errors:
        detail += f"; частичные ошибки: {len(errors)}"

    return ParserStartResponse(
        parser_id=parser_id,
        assignments=dict(clump.assignments),
        detail=detail,
    )


@parser_router.post("/stop/{parser_id}", response_model=ParserStopResponse)
async def parser_stop(parser_id: str) -> ParserStopResponse:
    job = _require_clump_job(parser_id)
    await job.clump.stop()
    await remove_clump(parser_id)
    job.finished = True
    job.cancelled = True
    _jobs.pop(parser_id, None)
    persist_delete_job(parser_id)
    return ParserStopResponse(parser_id=parser_id, detail="Clump остановлен")


@parser_router.get("/status/{parser_id}", response_model=ParserStatusItem)
async def parser_status(parser_id: str) -> ParserStatusItem:
    job = _require_clump_job(parser_id)
    return _status_from_job(parser_id, job)


@parser_router.get("/list", response_model=list[ParserStatusItem])
async def parser_list() -> list[ParserStatusItem]:
    return [_status_from_job(pid, job) for pid, job in list(_jobs.items())]


def _require_running_clump(parser_id: str) -> _ClumpJob:
    job = _require_clump_job(parser_id)
    if job.finished:
        raise HTTPException(
            status_code=409,
            detail="Clump уже остановлен, изменения списка каналов невозможны",
        )
    return job


@parser_router.get("/{parser_id}/channels", response_model=ChannelsListResponse)
async def parser_channels_list(parser_id: str) -> ChannelsListResponse:
    job = _require_clump_job(parser_id)
    by_session = {pc.session_name: list(pc.channels) for pc in job.clump.parser_client_list}
    return ChannelsListResponse(
        parser_id=parser_id,
        channel_list=job.clump.list_channels(),
        allowed_chat_ids=sorted(job.clump.all_allowed_chat_ids()),
        by_session=by_session,
    )


@parser_router.post(
    "/{parser_id}/add-channels", response_model=AddChannelsResponse
)
async def parser_add_channels(
    parser_id: str,
    body: ChannelsBody,
    async_mode: bool = Query(default=True, alias="async"),
) -> AddChannelsResponse:
    job = _require_running_clump(parser_id)

    log.info(
        "parser add-channels parser_id=%s count=%s async=%s",
        parser_id,
        len(body.channel_list),
        async_mode,
    )

    if async_mode:
        if get_use_pg_queue():
            action_id = uuid.uuid4().hex
            webhook_url = str(job.clump.webhook_url) if job.clump.webhook_url else None
            pg_result = await enqueue_parser_add_channels(
                parser_id=parser_id,
                channel_list=body.channel_list,
                webhook_url=webhook_url,
                action_id=action_id,
            )
            return AddChannelsResponse(
                parser_id=parser_id,
                channel_list=job.clump.list_channels(),
                action_id=pg_result.action_id,
                task_ids=pg_result.task_ids,
                async_mode=True,
            )

        action = enqueue_action(
            action_type="add_channels",
            parser_id=parser_id,
            payload={"channel_list": body.channel_list},
        )
        return AddChannelsResponse(
            parser_id=parser_id,
            channel_list=job.clump.list_channels(),
            action_id=action.get("id"),
            async_mode=True,
        )

    try:
        batch = await job.clump.add_channels_batch(body.channel_list)
    except ChannelQuotaExceeded as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    await job.clump.start()

    _persist_clump_state(parser_id, job.clump)

    return AddChannelsResponse(
        parser_id=parser_id,
        channel_list=batch["channel_list"],
        added=batch["added"],
        already_present=batch["already_present"],
        errors=batch["errors"],
        pending=batch.get("pending", []),
        assignments=dict(job.clump.assignments),
        async_mode=False,
    )


@parser_router.post(
    "/{parser_id}/remove-channels", response_model=RemoveChannelsResponse
)
async def parser_remove_channels(
    parser_id: str,
    body: ChannelsBody,
    async_mode: bool = Query(default=True, alias="async"),
) -> RemoveChannelsResponse:
    job = _require_running_clump(parser_id)

    if async_mode and get_use_pg_queue():
        action_id = uuid.uuid4().hex
        pg_result = await enqueue_parser_remove_channels(
            parser_id=parser_id,
            channel_list=body.channel_list,
            action_id=action_id,
        )
        return RemoveChannelsResponse(
            parser_id=parser_id,
            channel_list=job.clump.list_channels(),
            action_id=pg_result.action_id,
            task_ids=pg_result.task_ids,
            async_mode=True,
        )

    batch = await job.clump.remove_channels_batch(body.channel_list)
    _persist_clump_state(parser_id, job.clump)
    return RemoveChannelsResponse(
        parser_id=parser_id,
        channel_list=batch["channel_list"],
        removed=batch["removed"],
        not_found=batch["not_found"],
        errors=batch["errors"],
    )


@parser_router.get("/{parser_id}/config", response_model=ClumpConfigResponse)
async def parser_get_config(parser_id: str) -> ClumpConfigResponse:
    job = _require_clump_job(parser_id)
    return ClumpConfigResponse(parser_id=parser_id, config=job.clump.config.to_dict())


@parser_router.patch("/{parser_id}/config", response_model=ClumpConfigResponse)
async def parser_update_config(
    parser_id: str, body: ClumpConfigUpdate
) -> ClumpConfigResponse:
    job = _require_clump_job(parser_id)
    overrides = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else body.dict(exclude_none=True)
    snapshot = job.clump.update_config(**overrides)
    _persist_clump_state(parser_id, job.clump)
    return ClumpConfigResponse(parser_id=parser_id, config=snapshot)


def _find_account_job(session_name: str, parser_id: Optional[str]) -> tuple[str, _ClumpJob]:
    """Находит clump-job, содержащий аккаунт. parser_id опционален."""
    if parser_id:
        job = _require_clump_job(parser_id)
        if not job.clump.has_session(session_name):
            raise HTTPException(
                status_code=404,
                detail="Аккаунт с таким session_name не найден в указанном парсере",
            )
        return parser_id, job
    for pid, job in list(_jobs.items()):
        if job.clump.has_session(session_name):
            return pid, job
    raise HTTPException(
        status_code=404, detail="Аккаунт с таким session_name не найден"
    )


@parser_router.get("/accounts/all", response_model=AccountAllListResponse)
async def parser_accounts_all() -> AccountAllListResponse:
    rows = list_all_accounts_merged(_jobs)
    now = datetime.now(timezone.utc)
    pg_states = await fetch_pg_queue_states()
    rows = await overlay_account_rows(rows, pg_states=pg_states, now=now)
    accounts = [AccountFullSummary(**row) for row in rows]
    generated_at = now.isoformat().replace("+00:00", "Z")
    return AccountAllListResponse(
        total=len(accounts), accounts=accounts, generated_at=generated_at
    )


@parser_router.get("/accounts", response_model=AccountListResponse)
async def parser_accounts() -> AccountListResponse:
    now = datetime.now(timezone.utc)
    pg_states = await fetch_pg_queue_states()
    accounts: list[AccountSummary] = []
    for pid, job in list(_jobs.items()):
        for summary in job.clump.list_account_summaries():
            row = overlay_queue_state(
                dict(summary),
                pg_states.get(summary["session_name"]),
                now=now,
            )
            accounts.append(AccountSummary(parser_id=pid, **row))
    return AccountListResponse(total=len(accounts), accounts=accounts)


def _detail_row_for_overlay(detail: dict[str, Any]) -> dict[str, Any]:
    health = detail.get("health") or {}
    row = dict(detail)
    row.setdefault("flood_until", health.get("flood_until"))
    row.setdefault("flood_remaining_seconds", health.get("flood_remaining_seconds"))
    if row.get("last_error") is None:
        row["last_error"] = health.get("last_error")
    return row


@parser_router.get("/account-detail", response_model=AccountDetail)
async def parser_account_detail(
    session_name: str, parser_id: Optional[str] = None
) -> AccountDetail:
    pid, job = _find_account_job(session_name, parser_id)
    detail = job.clump.account_detail(session_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    now = datetime.now(timezone.utc)
    pg_states = await fetch_pg_queue_states()
    norm = normalize_session_name(session_name)
    row = overlay_queue_state(
        _detail_row_for_overlay(detail),
        pg_states.get(norm) or pg_states.get(session_name),
        now=now,
    )
    return AccountDetail(parser_id=pid, **row)


@parser_router.get("/account-channels", response_model=AccountChannelsResponse)
async def parser_account_channels(
    session_name: str, parser_id: Optional[str] = None
) -> AccountChannelsResponse:
    pid, job = _find_account_job(session_name, parser_id)
    channels = job.clump.account_channels(session_name)
    if channels is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return AccountChannelsResponse(
        parser_id=pid,
        session_name=session_name,
        channel_count=len(channels),
        channels=channels,
    )


@parser_router.patch("/account-meta", response_model=AccountDetail)
async def parser_account_meta(body: AccountMetaUpdate) -> AccountDetail:
    norm = normalize_session_name(body.session_name)
    update_account_fields(
        norm,
        display_name=body.display_name,
        description=body.description,
        max_channels=body.max_channels,
    )
    if body.parser_id:
        job = _require_clump_job(body.parser_id)
        try:
            job.clump.set_account_meta(
                body.session_name,
                display_name=body.display_name,
                description=body.description,
            )
        except ValueError:
            pass
        _persist_clump_state(body.parser_id, job.clump)
        pid, job = _find_account_job(body.session_name, body.parser_id)
        detail = job.clump.account_detail(body.session_name)
        if detail is None:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
        return AccountDetail(parser_id=pid, **detail)

    rows = list_all_accounts_merged(_jobs)
    row = next((r for r in rows if r["session_name"] == norm), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return AccountDetail(
        parser_id=row.get("parser_id") or "",
        session_name=norm,
        display_name=row["display_name"],
        description=row.get("description") or "",
        clump_name=row.get("clump_name"),
        running=row.get("running", False),
        channel_count=row.get("channel_count", 0),
        limits={
            "effective_max_channels": row.get("effective_max_channels"),
            "limit_source": row.get("limit_source"),
            "max_channels": row.get("max_channels"),
        },
        health={"status": row.get("status"), "banned": row.get("banned")},
    )


@parser_router.patch("/accounts/{session_name:path}/block", response_model=AccountFullSummary)
async def parser_account_block(
    session_name: str, body: AccountBlockUpdate
) -> AccountFullSummary:
    norm = normalize_session_name(session_name)
    set_admin_blocked(norm, blocked=body.blocked, reason=body.reason)
    rows = list_all_accounts_merged(_jobs)
    row = next((r for r in rows if r["session_name"] == norm), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return AccountFullSummary(**row)


@parser_router.patch("/accounts/{session_name:path}", response_model=AccountFullSummary)
async def parser_account_update(
    session_name: str, body: AccountUpdateBody
) -> AccountFullSummary:
    norm = normalize_session_name(session_name)
    update_account_fields(
        norm,
        display_name=body.display_name,
        description=body.description,
        max_channels=body.max_channels,
    )
    rows = list_all_accounts_merged(_jobs)
    row = next((r for r in rows if r["session_name"] == norm), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return AccountFullSummary(**row)


@parser_router.delete("/accounts/{session_name:path}")
async def parser_account_delete(
    session_name: str,
    migrate: bool = Query(default=True),
) -> dict[str, Any]:
    norm = normalize_session_name(session_name)
    for pid, job in list(_jobs.items()):
        sn = session_name if job.clump.has_session(session_name) else (
            norm if job.clump.has_session(norm) else None
        )
        if sn is None:
            continue
        try:
            await job.clump.remove_session_force(sn, migrate=migrate)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _persist_clump_state(pid, job.clump)
    await release_client(session_name)
    await release_client(norm)
    delete_account_full(norm)
    return {"ok": True, "session_name": norm, "deleted": True}


@parser_router.post("/{parser_id}/enroll-session", response_model=AccountFullSummary)
async def parser_enroll_session(
    parser_id: str, body: SessionBody
) -> AccountFullSummary:
    job = _require_running_clump(parser_id)
    norm = normalize_session_name(body.session_name)
    if not session_file_exists(norm):
        raise HTTPException(
            status_code=404,
            detail=f"Файл сессии не найден: {norm}.session",
        )
    upsert_account(norm, display_name=norm, source="manual")
    await job.clump.add_session(norm)
    await job.clump.start()
    _persist_clump_state(parser_id, job.clump)
    rows = list_all_accounts_merged(_jobs)
    row = next((r for r in rows if r["session_name"] == norm), None)
    if row is None:
        raise HTTPException(status_code=500, detail="Не удалось зарегистрировать аккаунт")
    return AccountFullSummary(**row)


@parser_router.get("/actions", response_model=ActionListResponse)
async def parser_actions_list(
    status: Optional[str] = None,
    parser_id: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> ActionListResponse:
    items = list_actions(
        status=status, parser_id=parser_id, action_type=action_type, limit=limit
    )
    actions = [ActionItemResponse(**item) for item in items]
    return ActionListResponse(total=len(actions), actions=actions)


@parser_router.get("/actions/{action_id}", response_model=ActionItemResponse)
async def parser_action_get(action_id: str) -> ActionItemResponse:
    item = get_action(action_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return ActionItemResponse(**item)


@parser_router.get("/queue/tasks/{task_id}", response_model=TaskQueueItemResponse)
async def parser_queue_task_get(task_id: int) -> TaskQueueItemResponse:
    task = await get_task_snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return _task_snapshot_to_response(task)


@parser_router.get("/queue/metrics", response_model=MetricsResponse)
async def parser_queue_metrics() -> MetricsResponse:
    return await get_queue_metrics()


@parser_router.get(
    "/queue/task-types",
    response_model=list[TaskTypeListItemResponse],
)
async def parser_queue_task_types_list() -> list[TaskTypeListItemResponse]:
    return await list_task_types()


@parser_router.get(
    "/queue/task-types/{code}",
    response_model=TaskTypeDetailResponse,
)
async def parser_queue_task_type_get(code: str) -> TaskTypeDetailResponse:
    return await get_task_type(code)


@parser_router.patch(
    "/queue/task-types/{code}",
    response_model=TaskTypeDetailResponse,
)
async def parser_queue_task_type_patch(
    code: str,
    body: TaskTypePatchRequest,
) -> TaskTypeDetailResponse:
    return await patch_task_type(code, body)


@parser_router.get("/settings", response_model=BalancerSettingsResponse)
async def parser_settings() -> BalancerSettingsResponse:
    """Глобальные дефолты балансировщика (из окружения).

    Per-clump переопределения доступны через GET/PATCH /{parser_id}/config.
    """
    settings = {
        "max_channels_per_session": get_max_channels_per_session(),
        "max_reconnects": get_session_max_reconnects(),
        "reconnect_backoff_base": get_session_reconnect_backoff_base(),
        "reconnect_backoff_max": get_session_reconnect_backoff_max(),
        "flood_migrate_threshold_seconds": get_session_flood_migrate_threshold_seconds(),
        "resolve_min_interval": get_session_resolve_min_interval(),
        "auto_migrate": get_session_auto_migrate(),
        "health_check_interval": get_session_health_check_interval(),
        "add_channels_per_hour": get_add_channels_per_hour(),
        "rebalance_enabled": get_rebalance_enabled(),
        "rebalance_idle_start_hour": get_rebalance_idle_start_hour(),
        "rebalance_idle_end_hour": get_rebalance_idle_end_hour(),
        "rebalance_high_watermark_ratio": get_rebalance_high_watermark_ratio(),
        "rebalance_low_watermark_ratio": get_rebalance_low_watermark_ratio(),
        "rebalance_min_gap_channels": get_rebalance_min_gap_channels(),
        "rebalance_max_moves_per_tick": get_rebalance_max_moves_per_tick(),
        "rebalance_cooldown_hours": get_rebalance_cooldown_hours(),
    }
    descriptions = {
        "max_channels_per_session": "Лимит каналов на один аккаунт (сессию)",
        "max_reconnects": (
            "Сколько подряд неуспешных подключений/авторизаций до перевода "
            "аккаунта в статус 'неактивный' (disconnected)"
        ),
        "reconnect_backoff_base": "Базовая пауза (сек) перед переподключением",
        "reconnect_backoff_max": "Максимальная пауза (сек) между переподключениями",
        "flood_migrate_threshold_seconds": (
            "Длительность FloodWait (сек), при которой каналы мигрируют на "
            "другой аккаунт — время ожидания до миграции"
        ),
        "resolve_min_interval": (
            "Минимальный интервал (сек) между resolve-запросами на аккаунт "
            "(антифлуд при добавлении каналов)"
        ),
        "auto_migrate": "Автоматически переносить каналы с упавших аккаунтов",
        "health_check_interval": "Период (сек) фонового мониторинга здоровья",
        "add_channels_per_hour": "Лимит успешных добавлений каналов на аккаунт в час (0 = без лимита)",
        "rebalance_enabled": "Фоновый rebalance в тихое окно",
        "rebalance_idle_start_hour": "Начало тихого окна rebalance (UTC, час)",
        "rebalance_idle_end_hour": "Конец тихого окна rebalance (UTC, час)",
        "rebalance_high_watermark_ratio": "Доля лимита, выше которой сессия считается перегруженной",
        "rebalance_low_watermark_ratio": "Доля лимита, ниже которой сессия может принять каналы",
        "rebalance_min_gap_channels": "Минимальный разрыв загрузки между min/max для rebalance",
        "rebalance_max_moves_per_tick": "Максимум переносов каналов за один тик rebalance",
        "rebalance_cooldown_hours": "Не переносить тот же канал чаще чем раз в N часов",
    }
    return BalancerSettingsResponse(settings=settings, descriptions=descriptions)


@parser_router.post(
    "/{parser_id}/add-session", response_model=SessionOpResponse
)
async def parser_add_session(parser_id: str, body: SessionBody) -> SessionOpResponse:
    job = _require_running_clump(parser_id)
    await job.clump.add_session(body.session_name)
    _persist_clump_state(parser_id, job.clump)
    return SessionOpResponse(
        parser_id=parser_id,
        session_name_list=list(job.clump.session_name_list),
        detail=f"Сессия {body.session_name} добавлена в clump",
    )


@parser_router.post(
    "/{parser_id}/remove-session", response_model=SessionOpResponse
)
async def parser_remove_session(parser_id: str, body: SessionBody) -> SessionOpResponse:
    job = _require_running_clump(parser_id)
    try:
        await job.clump.remove_session(body.session_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _persist_clump_state(parser_id, job.clump)
    return SessionOpResponse(
        parser_id=parser_id,
        session_name_list=list(job.clump.session_name_list),
        detail=f"Сессия {body.session_name} удалена из clump",
    )


@parser_router.delete("/{parser_id}", response_model=ParserStopResponse)
async def parser_delete(parser_id: str) -> ParserStopResponse:
    job = _jobs.get(parser_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Парсер с таким id не найден")

    await job.clump.stop()
    await remove_clump(parser_id)
    _jobs.pop(parser_id, None)
    persist_delete_job(parser_id)
    return ParserStopResponse(parser_id=parser_id, detail="Запись clump удалена")


async def _execute_action(item: dict[str, Any]) -> None:
    action_id = item["id"]
    parser_id = item["parser_id"]
    payload = item["payload"]
    job = _jobs.get(parser_id)
    if job is None:
        raise ValueError(f"Парсер {parser_id} не найден")
    action_type = item["action_type"]
    if action_type == "add_channels":
        refs = list(payload.get("channel_list") or [])
        update_action_progress(action_id, 0, len(refs))
        batch = await job.clump.add_channels_batch(refs)
        await job.clump.start()
        _persist_clump_state(parser_id, job.clump)
        update_action_progress(action_id, len(refs), len(refs))
        if batch.get("errors"):
            log.warning(
                "action %s add_channels partial errors: %s",
                action_id,
                batch["errors"][:3],
            )
    elif action_type == "remove_channels":
        refs = list(payload.get("channel_list") or [])
        update_action_progress(action_id, 0, len(refs))
        batch = await job.clump.remove_channels_batch(refs)
        _persist_clump_state(parser_id, job.clump)
        update_action_progress(action_id, len(refs), len(refs))
    else:
        raise ValueError(f"Неизвестный тип задачи: {action_type}")


def setup_parser_services() -> None:
    register_action_handler(_execute_action)
    start_action_worker()


async def restore_persisted_parsers() -> None:
    """После перезапуска процесса — поднять clump из JSON-хранилища."""

    if not is_persistence_enabled():
        return
    if not _env_telegram_configured():
        log.warning(
            "Восстановление парсеров пропущено: не заданы API_ID и/или API_HASH"
        )
        return

    records = load_persisted_jobs()
    for rec in records:
        rec = normalize_persisted_record(rec)
        parser_id = rec.get("parser_id")
        if not isinstance(parser_id, str) or not parser_id:
            continue
        if parser_id in _jobs:
            continue

        session_list = rec.get("session_name_list")
        if not isinstance(session_list, list) or not session_list:
            log.warning("Пропуск записи без session_name_list: %s", rec)
            continue

        webhook_url = rec.get("webhook_url")
        channel_list = rec.get("channel_list")
        if not webhook_url or not isinstance(channel_list, list):
            log.warning("Пропуск некорректной записи парсера: %s", rec)
            continue

        ch_list = [str(x) for x in channel_list]
        if not ch_list:
            log.warning("Пропуск записи без каналов: %s", parser_id)
            continue

        try:
            clump = await get_or_create_clump(
                parser_id,
                [str(s) for s in session_list],
                str(webhook_url),
                clump_name=str(rec.get("clump_name") or parser_id),
            )
            clump.restore_from_record(rec)
            await clump.start()
            _jobs[parser_id] = _ClumpJob(clump=clump, parser_id=parser_id)
            log.info("Восстановлен clump parser_id=%s sessions=%s", parser_id, len(session_list))
        except Exception:
            log.exception(
                "Не удалось восстановить clump из хранилища (id=%s)", parser_id
            )
