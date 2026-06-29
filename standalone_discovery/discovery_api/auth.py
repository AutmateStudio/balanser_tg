from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

import telethon.errors
from telethon import TelegramClient
from telethon.sessions import StringSession

from discovery_api.config import get_api_hash, get_api_id
from discovery_api.session_store import delete_session, init_session_db, list_restorable_sessions, upsert_session


log = logging.getLogger(__name__)


SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/app/sessions")
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _resolve_session_path(session_name: str) -> str:
    """
    Безопасно строит абсолютный путь к Telethon `.session`-файлу
    в каталоге `SESSIONS_DIR`. Имя проверяется регуляркой, чтобы
    исключить traversal через `..` или абсолютные пути.
    """
    if not session_name or not _SESSION_NAME_RE.fullmatch(session_name):
        raise ValueError(
            "session_name должен содержать только латинские буквы, цифры, '_' и '-' (длина 1-64)"
        )
    return os.path.join(SESSIONS_DIR, session_name)


def _save_string_session_to_file(string_session: str, session_name: str) -> str:
    """
    Конвертирует Telethon `StringSession` в SQLite-сессию и сохраняет её
    в `<SESSIONS_DIR>/<session_name>.session`. Если файл уже существует —
    он перезаписывается, чтобы Telethon не пытался смешать несовместимые
    auth-ключи. Возвращает абсолютный путь к созданному файлу.
    """
    target_path = _resolve_session_path(session_name)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    sqlite_path = target_path + ".session"
    try:
        os.remove(sqlite_path)
    except FileNotFoundError:
        pass

    src = StringSession(string_session)
    dst_client = TelegramClient(target_path, get_api_id(), get_api_hash())
    try:
        dst_client.session.set_dc(src.dc_id, src.server_address, src.port)
        dst_client.session.auth_key = src.auth_key
        dst_client.session.save()
    finally:
        try:
            dst_client.session.close()
        except Exception:
            pass
    return sqlite_path


@dataclass
class QRSession:
    session_id: str
    client: TelegramClient
    qr_login: Optional[object] = None
    qr_url: str = ""
    status: str = "pending"
    result: Optional[Dict] = None
    session_name: Optional[str] = None
    _wait_task: Optional[asyncio.Task] = field(default=None, repr=False)


_active_sessions: Dict[str, QRSession] = {}


async def create_qr_session(session_name: Optional[str] = None) -> QRSession:
    if session_name is not None:
        # Валидируем заранее, чтобы не отдавать QR, который потом
        # окажется бесполезным из-за невалидного имени файла.
        _resolve_session_path(session_name)

    init_session_db()
    client = TelegramClient(StringSession(), get_api_id(), get_api_hash())
    await client.connect()
    qr_login = await client.qr_login()

    session_id = uuid.uuid4().hex
    session = QRSession(
        session_id=session_id,
        client=client,
        qr_login=qr_login,
        qr_url=qr_login.url,
        session_name=session_name,
    )
    _active_sessions[session_id] = session
    upsert_session(session_id=session_id, status="pending", qr_url=session.qr_url)
    session._wait_task = asyncio.create_task(_wait_for_scan(session))
    return session


async def _wait_for_scan(session: QRSession) -> None:
    deadline = asyncio.get_event_loop().time() + 300
    while True:
        if asyncio.get_event_loop().time() >= deadline:
            session.status = "expired"
            await session.client.disconnect()
            delete_session(session.session_id)
            return
        try:
            user = await session.qr_login.wait(timeout=30)
            me = await session.client.get_me()
            phone = f"+{me.phone}" if me.phone else f"id{me.id}"
            session_string = session.client.session.save()

            saved_file: Optional[str] = None
            saved_error: Optional[str] = None
            if session.session_name:
                try:
                    saved_file = await asyncio.to_thread(
                        _save_string_session_to_file,
                        session_string,
                        session.session_name,
                    )
                except Exception as exc:  # pragma: no cover - защитный путь
                    saved_error = str(exc)
                    log.exception(
                        "Не удалось сохранить .session-файл для session_name=%s",
                        session.session_name,
                    )

            session.status = "success"
            session.result = {
                "phone": phone,
                "user_id": me.id,
                "user_name": f"{me.first_name or ''} {me.last_name or ''}".strip() or phone,
                "session_string": session_string,
                "session_file": saved_file,
                "session_file_error": saved_error,
            }
            upsert_session(
                session_id=session.session_id,
                status="success",
                qr_url=session.qr_url,
                phone=phone,
                user_id=me.id,
                user_name=session.result["user_name"],
                session_string=session_string,
            )
            if session.session_name:
                from discovery_api.account_registry import register_account_after_qr
                from app_balance.queue.accounts_sync import sync_accounts_to_pg_best_effort

                register_account_after_qr(session.session_name)
                await sync_accounts_to_pg_best_effort(
                    context=f"qr:{session.session_name}"
                )
            return
        except telethon.errors.SessionPasswordNeededError:
            session.status = "2fa_required"
            await session.client.disconnect()
            delete_session(session.session_id)
            return
        except asyncio.TimeoutError:
            await session.qr_login.recreate()
            session.qr_url = session.qr_login.url
        except Exception:
            session.status = "error"
            await session.client.disconnect()
            delete_session(session.session_id)
            return


def get_qr_session(session_id: str) -> Optional[QRSession]:
    return _active_sessions.get(session_id)


async def cleanup_session(session_id: str) -> None:
    session = _active_sessions.pop(session_id, None)
    if session is None:
        return
    if session._wait_task and not session._wait_task.done():
        session._wait_task.cancel()
    try:
        await session.client.disconnect()
    except Exception:
        pass
    delete_session(session_id)


async def restore_active_sessions() -> int:
    init_session_db()
    restored = 0
    api_id = get_api_id()
    api_hash = get_api_hash()
    for item in list_restorable_sessions():
        session_string = item.get("session_string") or ""
        if not session_string:
            continue
        session_id = item["session_id"]
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        try:
            await client.connect()
            me = await client.get_me()
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            delete_session(session_id)
            continue
        _active_sessions[session_id] = QRSession(
            session_id=session_id,
            client=client,
            status="success",
            qr_url=item.get("qr_url") or "",
            result={
                "phone": item.get("phone") or (f"+{me.phone}" if getattr(me, "phone", None) else f"id{me.id}"),
                "user_id": getattr(me, "id", item.get("user_id")),
                "user_name": item.get("user_name") or f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip(),
                "session_string": session_string,
            },
        )
        restored += 1
    return restored

