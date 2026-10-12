from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from discovery_api.client_sessions.errors import ClientSessionError, SessionRevokedError
from discovery_api.client_sessions.service import get_service
from discovery_api.client_sessions.store import ACCOUNT_ID_RE

router = APIRouter(prefix="/discovery-api/client-sessions", tags=["client-sessions"])

PEER_RE = re.compile(r"^[0-9]{1,20}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_:-]{8,80}$")


class QrCreateRequest(BaseModel):
    remote_account_id: str = Field(..., min_length=8, max_length=80)


class PasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class SendRequest(BaseModel):
    peer_id: str | None = None
    username: str | None = None
    text: str = Field(..., min_length=1, max_length=4096)
    idempotency_key: str


class WatchRequest(BaseModel):
    peer_id: str
    username: str | None = None


def _account_or_422(remote_account_id: str) -> str:
    if not ACCOUNT_ID_RE.fullmatch(remote_account_id):
        raise HTTPException(status_code=422, detail="Некорректный идентификатор аккаунта")
    return remote_account_id


def _peer(peer_id: str | None, username: str | None) -> tuple[str | None, str | None]:
    clean_peer = peer_id.strip() if peer_id else None
    clean_username = username.strip().lstrip("@") if username else None
    if clean_peer and not PEER_RE.fullmatch(clean_peer):
        raise HTTPException(status_code=422, detail="Некорректный peer")
    if clean_username and not USERNAME_RE.fullmatch(clean_username):
        raise HTTPException(status_code=422, detail="Некорректный username")
    if not clean_peer and not clean_username:
        raise HTTPException(status_code=422, detail="Нужен peer или username")
    return clean_peer, clean_username


@router.post("/qr")
async def create_qr(body: QrCreateRequest, background: BackgroundTasks) -> dict:
    remote_account_id = _account_or_422(body.remote_account_id)
    try:
        created = await get_service().open_qr(remote_account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    background.add_task(get_service().await_qr, created["qr_session_id"])
    return created


@router.get("/qr/{qr_session_id}")
async def qr_status(qr_session_id: str) -> dict:
    try:
        return get_service().qr_view(qr_session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="QR-сессия не найдена") from None


@router.post("/qr/{qr_session_id}/2fa")
async def qr_2fa(qr_session_id: str, body: PasswordRequest) -> dict:
    try:
        return await get_service().submit_password(qr_session_id, body.password)
    except KeyError:
        raise HTTPException(status_code=404, detail="QR-сессия не найдена") from None
    except ClientSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/qr/{qr_session_id}")
async def cancel_qr(qr_session_id: str) -> dict:
    try:
        return await get_service().cancel_qr(qr_session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="QR-сессия не найдена") from None


@router.delete("/{remote_account_id}")
async def revoke_account(remote_account_id: str) -> dict:
    _account_or_422(remote_account_id)
    try:
        return await get_service().revoke(remote_account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Аккаунт не найден") from None


@router.post("/{remote_account_id}/messages")
async def send_message(remote_account_id: str, body: SendRequest) -> dict:
    _account_or_422(remote_account_id)
    if not IDEMPOTENCY_RE.fullmatch(body.idempotency_key):
        raise HTTPException(status_code=422, detail="Некорректный ключ идемпотентности")
    peer_id, username = _peer(body.peer_id, body.username)
    try:
        return await get_service().send_dm(
            remote_account_id,
            peer_id,
            username,
            body.text,
            body.idempotency_key,
        )
    except SessionRevokedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/{remote_account_id}/history")
async def history(
    remote_account_id: str,
    peer_id: str | None = None,
    username: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    _account_or_422(remote_account_id)
    peer, name = _peer(peer_id, username)
    safe_limit = min(max(limit, 1), 100)
    if cursor and not cursor.isdigit():
        raise HTTPException(status_code=422, detail="Некорректный курсор")
    try:
        return await get_service().history(remote_account_id, peer, name, cursor, safe_limit)
    except SessionRevokedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.post("/{remote_account_id}/watch")
async def watch(remote_account_id: str, body: WatchRequest) -> dict:
    _account_or_422(remote_account_id)
    peer, username = _peer(body.peer_id, body.username)
    target = peer or f"username:{username}"
    try:
        await get_service().watch(remote_account_id, target, username)
    except SessionRevokedError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    return {"ok": True, "peer_id": target}
