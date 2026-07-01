from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from discovery_api.add_channel_via_link_or_name import add_channel_via_link
from discovery_api.chat_resolve import ChannelHasNoDiscussionError, ChatAccessError
from discovery_api.auth import cleanup_session, create_qr_session, get_qr_session
from discovery_api.config import get_use_pg_queue
from discovery_api.discovery import (
    DiscoveredChannel,
    DiscoveredGroup,
    discover_unified_on_client,
    persist_unified_discovery,
)
from discovery_api.queue.producer import enqueue_telegram_discover
from discovery_api.send_bot_message import send_bot_message
from discovery_api.session_registry import get_or_create_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery-api", tags=["discovery-api"])


class QRCreateRequest(BaseModel):
    session_name: Optional[str] = Field(
        default=None,
        description=(
            "Имя файла Telethon-сессии для автосохранения после успешной "
            "QR-авторизации. Файл будет создан как "
            "`<SESSIONS_DIR>/<session_name>.session` (по умолчанию "
            "`/app/sessions/<session_name>.session`). Допустимые символы: "
            "латинские буквы, цифры, '_' и '-' (длина 1-64). Если не указан — "
            "файл не сохраняется, а `session_string` нужно сохранить вручную."
        ),
    )


class QRCreateResponse(BaseModel):
    session_id: str
    qr_url: str
    status: str
    session_name: Optional[str] = None


class QRStatusResponse(BaseModel):
    session_id: str
    status: str
    qr_url: str
    phone: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    session_string: Optional[str] = None
    session_file: Optional[str] = None
    session_file_error: Optional[str] = None


class DiscoveryRequest(BaseModel):
    session_name: str = Field(
        ...,
        description="Имя или путь к Telethon .session-файлу на сервере (без расширения)",
    )
    query: str = Field(...)
    first_pass_limit: int = Field(default=10, ge=1, le=100)
    similarity_depth: int = Field(default=2, ge=0, le=5)
    include_global_search: bool = Field(
        default=True,
        description=(
            "Дополнительно искать broadcast-каналы по тексту сообщений "
            "(messages.SearchGlobal), а не только по названию (contacts.Search). "
            "Находит каналы, где запрос реально обсуждается — там и есть "
            "потенциальные клиенты. Стоит +1 запрос к Telegram на весь /discover."
        ),
    )
    include_groups: bool = Field(
        default=True,
        description=(
            "Помимо broadcast-каналов возвращать также группы/супергруппы/чаты "
            "(megagroup, gigagroup, классические Chat). По умолчанию включено. "
            "При включении растёт нагрузка на Telegram."
        ),
    )


class ChannelItem(BaseModel):
    peer_id: int
    title: str
    username: Optional[str] = None
    participants_count: Optional[int] = None
    depth: int
    source: str
    recommended_by: Optional[int] = None
    score: int = 0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    score_signals: Dict[str, Any] = Field(default_factory=dict)
    score_hard_flags: Dict[str, bool] = Field(default_factory=dict)
    access_hash: Optional[int] = None
    verified: Optional[bool] = None
    scam: Optional[bool] = None
    fake: Optional[bool] = None
    restricted: Optional[bool] = None
    megagroup: Optional[bool] = None
    gigagroup: Optional[bool] = None
    broadcast: Optional[bool] = None
    forum: Optional[bool] = None
    signatures: Optional[bool] = None
    noforwards: Optional[bool] = None
    slowmode_enabled: Optional[bool] = None
    creator: Optional[bool] = None
    has_link: Optional[bool] = None
    has_geo: Optional[bool] = None
    join_to_send: Optional[bool] = None
    join_request: Optional[bool] = None
    created_at: Optional[str] = None
    restriction_reason: Optional[List[Dict[str, Any]]] = None
    about: Optional[str] = None
    online_count: Optional[int] = None
    admins_count: Optional[int] = None
    kicked_count: Optional[int] = None
    banned_count: Optional[int] = None
    linked_chat_id: Optional[int] = None
    slowmode_seconds: Optional[int] = None
    pinned_msg_id: Optional[int] = None
    read_inbox_max_id: Optional[int] = None
    source_peer_id: Optional[int] = None
    listen_chat_id: Optional[int] = None
    entity_kind: Optional[str] = None
    listen_mode: Optional[str] = None
    source_joined: Optional[bool] = None
    listen_joined: Optional[bool] = None
    has_listen_access: Optional[bool] = None
    access_note: Optional[str] = None


class GroupItem(BaseModel):
    peer_id: int
    title: str
    username: Optional[str] = None
    participants_count: Optional[int] = None
    depth: int
    source: str
    recommended_by: Optional[int] = None
    matched_seed: Optional[str] = None
    score_total: int = 0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    score_signals: Dict[str, Any] = Field(default_factory=dict)
    score_hard_flags: Dict[str, bool] = Field(default_factory=dict)
    access_hash: Optional[int] = None
    verified: Optional[bool] = None
    scam: Optional[bool] = None
    fake: Optional[bool] = None
    restricted: Optional[bool] = None
    megagroup: Optional[bool] = None
    gigagroup: Optional[bool] = None
    broadcast: Optional[bool] = None
    forum: Optional[bool] = None
    signatures: Optional[bool] = None
    noforwards: Optional[bool] = None
    slowmode_enabled: Optional[bool] = None
    creator: Optional[bool] = None
    has_link: Optional[bool] = None
    has_geo: Optional[bool] = None
    join_to_send: Optional[bool] = None
    join_request: Optional[bool] = None
    created_at: Optional[str] = None
    restriction_reason: Optional[List[Dict[str, Any]]] = None


class PersistStatsResponse(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped_no_discussion: int = 0
    channel_ids: List[int] = Field(default_factory=list)


class DiscoveryResponse(BaseModel):
    query: str
    total: int
    depth_stats: Dict[int, int]
    channels: List[ChannelItem]
    groups: List[GroupItem] = Field(default_factory=list)
    seeds: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    persist: Optional[PersistStatsResponse] = None
    task_id: Optional[int] = Field(
        default=None,
        description="id задачи PG-очереди (async); результат — GET /parser/queue/tasks/{task_id}",
    )
    action_id: Optional[str] = None
    async_mode: bool = False
    deprecated: bool = False


class GroupDiscoveryRequest(BaseModel):
    session_name: str = Field(
        ...,
        description="Имя или путь к Telethon .session-файлу на сервере (без расширения)",
    )
    word: str = Field(...)
    limit: int = Field(default=20, ge=1, le=100)
    depth: int = Field(default=2, ge=0, le=5)


class GroupDiscoveryResponse(BaseModel):
    query: str
    seeds: List[str]
    total: int
    depth_stats: Dict[int, int]
    groups: List[GroupItem]
    errors: List[str] = Field(default_factory=list)
    task_id: Optional[int] = Field(
        default=None,
        description="id задачи PG-очереди (async); результат — GET /parser/queue/tasks/{task_id}",
    )
    action_id: Optional[str] = Field(
        default=None,
        description="Корреляционный id async-запроса",
    )
    async_mode: bool = Field(
        default=False,
        description="true — поиск поставлен в очередь, groups пуст до завершения задачи",
    )


class AddChannelByLinkRequest(BaseModel):
    session_name: str = Field(
        ...,
        description="Имя или путь к Telethon .session-файлу на сервере (без расширения)",
    )
    link: str = Field(...)


class AddChannelByLinkSessionFileRequest(BaseModel):
    session_file: str = Field(..., description="Path or name for Telethon .session file")
    link: str = Field(...)


class BotMessageRequest(BaseModel):
    chat_id: int = Field(..., description="Telegram chat_id получателя")
    text: Optional[str] = Field(default=None, description="Текст или HTML-caption")
    image_url: Optional[str] = Field(default=None, description="URL изображения")
    layout: Optional[str] = Field(
        default="inline",
        description="Тип кнопок: inline или keyboard",
    )
    buttons: List[Any] = Field(default_factory=list)


class BotMessageResponse(BaseModel):
    ok: bool
    message_id: Optional[int] = None
    chat_id: Optional[int] = None


def _channel_item(c: DiscoveredChannel) -> ChannelItem:
    return ChannelItem(
        peer_id=c.peer_id,
        title=c.title,
        username=c.username,
        participants_count=c.participants_count,
        depth=c.depth,
        source=c.source,
        recommended_by=c.recommended_by,
        score=c.score_total,
        score_breakdown=c.score_breakdown,
        score_signals=c.score_signals,
        score_hard_flags=c.score_hard_flags,
        **(c.meta or {}),
    )


def _group_item(g: DiscoveredGroup) -> GroupItem:
    return GroupItem(
        peer_id=g.peer_id,
        title=g.title,
        username=g.username,
        participants_count=g.participants_count,
        depth=g.depth,
        source=g.source,
        recommended_by=g.recommended_by,
        matched_seed=g.matched_seed,
        score_total=g.score_total,
        score_breakdown=g.score_breakdown,
        score_signals=g.score_signals,
        score_hard_flags=g.score_hard_flags,
        **(g.meta or {}),
    )


def _discovery_response_from_unified(
    result,
    *,
    persist=None,
    task_id: int | None = None,
    action_id: str | None = None,
    async_mode: bool = False,
    deprecated: bool = False,
) -> DiscoveryResponse:
    persist_resp = None
    if persist is not None:
        persist_resp = PersistStatsResponse(
            inserted=persist.inserted,
            updated=persist.updated,
            skipped_no_discussion=persist.skipped_no_discussion,
            channel_ids=list(persist.channel_ids),
        )
    return DiscoveryResponse(
        query=result.query,
        total=result.total,
        depth_stats=result.depth_stats,
        channels=[_channel_item(c) for c in result.channels],
        groups=[_group_item(g) for g in result.groups],
        seeds=list(result.seeds),
        errors=list(result.errors),
        persist=persist_resp,
        task_id=task_id,
        action_id=action_id,
        async_mode=async_mode,
        deprecated=deprecated,
    )


@router.post("/auth/qr", response_model=QRCreateResponse)
async def auth_qr_create(
    req: QRCreateRequest = Body(default_factory=QRCreateRequest),
):
    try:
        session = await create_qr_session(session_name=req.session_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка создания QR-сессии: {e}")
    return QRCreateResponse(
        session_id=session.session_id,
        qr_url=session.qr_url,
        status=session.status,
        session_name=session.session_name,
    )


@router.get("/auth/qr/{session_id}/status", response_model=QRStatusResponse)
async def auth_qr_status(session_id: str):
    session = get_qr_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="QR-сессия не найдена или истекла")
    response = QRStatusResponse(session_id=session.session_id, status=session.status, qr_url=session.qr_url)
    if session.status == "success" and session.result:
        response.phone = session.result.get("phone")
        response.user_id = session.result.get("user_id")
        response.user_name = session.result.get("user_name")
        response.session_string = session.result.get("session_string")
        response.session_file = session.result.get("session_file")
        response.session_file_error = session.result.get("session_file_error")
    return response


@router.delete("/auth/qr/{session_id}")
async def auth_qr_delete(session_id: str):
    await cleanup_session(session_id)
    return {"ok": True}


@router.post("/discover", response_model=DiscoveryResponse)
async def discover(
    req: DiscoveryRequest,
    async_mode: bool = Query(
        default=True,
        alias="async",
        description="При true и USE_PG_QUEUE — задача в PG-очередь с резервом аккаунта и upsert в БД",
    ),
):
    if async_mode and get_use_pg_queue():
        action_id = uuid.uuid4().hex
        pg_result = await enqueue_telegram_discover(
            session_name=req.session_name,
            query=req.query,
            first_pass_limit=req.first_pass_limit,
            similarity_depth=req.similarity_depth,
            include_global_search=req.include_global_search,
            include_groups=req.include_groups,
            action_id=action_id,
        )
        if pg_result.task_id is None:
            return DiscoveryResponse(
                query=req.query,
                total=0,
                depth_stats={},
                channels=[],
                groups=[],
                errors=[
                    "Не удалось поставить задачу: аккаунт не найден в PG "
                    f"(session_name={req.session_name!r}) или пустой query"
                ],
            )
        return DiscoveryResponse(
            query=req.query,
            total=0,
            depth_stats={},
            channels=[],
            groups=[],
            errors=[],
            task_id=pg_result.task_id,
            action_id=pg_result.action_id,
            async_mode=True,
        )

    try:
        client = await get_or_create_client(req.session_name)
        result = await discover_unified_on_client(
            client,
            req.query,
            search_limit=req.first_pass_limit,
            max_depth=req.similarity_depth,
            include_global_search=req.include_global_search,
            include_groups=req.include_groups,
        )
        persist_stats = await persist_unified_discovery(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка discovery: {e}") from e

    return _discovery_response_from_unified(
        result,
        persist=persist_stats,
        async_mode=False,
    )


@router.post("/discover-groups", response_model=DiscoveryResponse, deprecated=True)
async def discover_groups_endpoint(
    req: GroupDiscoveryRequest,
    async_mode: bool = Query(default=True, alias="async"),
):
    """Deprecated: используйте POST /discover (query=word)."""
    log.warning("POST /discover-groups deprecated — используйте POST /discover")
    discover_req = DiscoveryRequest(
        session_name=req.session_name,
        query=req.word,
        first_pass_limit=req.limit,
        similarity_depth=req.depth,
        include_global_search=True,
        include_groups=True,
    )
    response = await discover(discover_req, async_mode=async_mode)
    response.deprecated = True
    return response


@router.post("/add-channel-by-link", response_model=ChannelItem)
async def add_channel_by_link(req: AddChannelByLinkRequest):
    client = await get_or_create_client(req.session_name)
    try:
        payload = await add_channel_via_link(client=client, link=req.link)
    except ChannelHasNoDiscussionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ChatAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка добавления канала: {e}")

    return ChannelItem(**payload)


@router.post("/add-channel-by-link-session-file", response_model=ChannelItem)
async def add_channel_by_link_session_file(req: AddChannelByLinkSessionFileRequest):
    client = await get_or_create_client(req.session_file)
    try:
        payload = await add_channel_via_link(client=client, link=req.link)
    except ChannelHasNoDiscussionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ChatAccessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка добавления канала по session-файлу: {e}")

    return ChannelItem(**payload)


@router.post("/bot/send-message", response_model=BotMessageResponse)
async def send_bot_message_endpoint(req: BotMessageRequest):
    if hasattr(req, "model_dump"):
        payload = req.model_dump(exclude_none=True)
    else:
        payload = req.dict(exclude_none=True)
    try:
        result = send_bot_message(chat_id=req.chat_id, message=payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка отправки сообщения ботом: {e}") from e

    result_chat = getattr(result, "chat", None)
    return BotMessageResponse(
        ok=True,
        message_id=getattr(result, "message_id", None),
        chat_id=getattr(result_chat, "id", req.chat_id),
    )

