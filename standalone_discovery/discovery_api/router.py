from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from discovery_api.add_channel_via_link_or_name import add_channel_via_link
from discovery_api.chat_resolve import ChannelHasNoDiscussionError, ChatAccessError
from discovery_api.auth import cleanup_session, create_qr_session, get_qr_session
from discovery_api.config import get_api_hash, get_api_id
from discovery_api.discovery import discover_channels, discover_groups
from discovery_api.send_bot_message import send_bot_message
from discovery_api.session_registry import get_or_create_client, get_session_string

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
        default=False,
        description=(
            "Помимо broadcast-каналов возвращать также группы/супергруппы/чаты "
            "(megagroup, gigagroup, классические Chat). По умолчанию выключено — "
            "/discover отдаёт только каналы. При включении растёт нагрузка на "
            "Telegram (для megagroup добавляется выборка участников)."
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


class DiscoveryResponse(BaseModel):
    query: str
    total: int
    depth_stats: Dict[int, int]
    channels: List[ChannelItem]


class GroupDiscoveryRequest(BaseModel):
    session_name: str = Field(
        ...,
        description="Имя или путь к Telethon .session-файлу на сервере (без расширения)",
    )
    word: str = Field(...)
    limit: int = Field(default=20, ge=1, le=100)
    depth: int = Field(default=2, ge=0, le=5)


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


class GroupDiscoveryResponse(BaseModel):
    query: str
    seeds: List[str]
    total: int
    depth_stats: Dict[int, int]
    groups: List[GroupItem]
    errors: List[str] = Field(default_factory=list)


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
async def discover(req: DiscoveryRequest):
    try:
        session_string = await get_session_string(req.session_name)
        result = await discover_channels(
            session_string=session_string,
            api_id=get_api_id(),
            api_hash=get_api_hash(),
            query=req.query,
            search_limit=req.first_pass_limit,
            max_depth=req.similarity_depth,
            include_global_search=req.include_global_search,
            include_groups=req.include_groups,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка discovery: {e}")
    return DiscoveryResponse(
        query=result.query,
        total=result.total,
        depth_stats=result.depth_stats,
        channels=[
            ChannelItem(
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
            for c in result.channels
        ],
    )


@router.post("/discover-groups", response_model=GroupDiscoveryResponse)
async def discover_groups_endpoint(req: GroupDiscoveryRequest):
    try:
        session_string = await get_session_string(req.session_name)
    except Exception as e:
        return GroupDiscoveryResponse(
            query=req.word,
            seeds=[],
            total=0,
            depth_stats={},
            groups=[],
            errors=[f"Не удалось загрузить session '{req.session_name}': {e!s}"],
        )

    try:
        result = await discover_groups(
            session_string=session_string,
            api_id=get_api_id(),
            api_hash=get_api_hash(),
            word=req.word,
            search_limit=req.limit,
            max_depth=req.depth,
        )
    except Exception as e:
        return GroupDiscoveryResponse(
            query=req.word,
            seeds=[],
            total=0,
            depth_stats={},
            groups=[],
            errors=[f"Ошибка поиска групп: {e!s}"],
        )

    return GroupDiscoveryResponse(
        query=result.query,
        seeds=result.seeds,
        total=result.total,
        depth_stats=result.depth_stats,
        groups=[
            GroupItem(
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
            for g in result.groups
        ],
        errors=list(result.errors),
    )


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

