"""Чтение сведений о Telethon `.session`-файлах (имя, телефон через get_me)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from telethon import TelegramClient

from discovery_api.account_registry import (
    normalize_session_name,
    scan_sessions_dir,
    session_file_exists,
    session_file_path,
)
from discovery_api.config import get_api_hash, get_api_id
from discovery_api.session_registry import find_registered_client

log = logging.getLogger(__name__)


def _telethon_session_base(session_name: str) -> str:
    """Путь к сессии для TelegramClient (без суффикса `.session`)."""
    norm = normalize_session_name(session_name)
    return session_file_path(norm)[: -len(".session")]


def _session_file_name(session_name: str) -> str:
    return f"{normalize_session_name(session_name)}.session"


async def probe_session_info(session_name: str) -> dict[str, Any]:
    """Возвращает имя сессии, имя файла и телефон (если сессия авторизована)."""
    norm = normalize_session_name(session_name)
    if not session_file_exists(norm):
        raise FileNotFoundError(f"Файл сессии не найден: {norm}.session")

    row: dict[str, Any] = {
        "session_name": norm,
        "session_file": _session_file_name(norm),
        "phone": None,
        "error": None,
    }

    client: Optional[TelegramClient] = find_registered_client(norm)
    owned = client is None
    if owned:
        client = TelegramClient(_telethon_session_base(norm), int(get_api_id()), get_api_hash())
        try:
            await client.connect()
        except Exception as exc:
            log.warning("Не удалось подключить сессию %s: %s", norm, exc)
            row["error"] = str(exc)
            return row

    assert client is not None
    try:
        if not await client.is_user_authorized():
            row["error"] = "Сессия не авторизована"
            return row
        me = await client.get_me()
        if me is None:
            row["error"] = "get_me вернул пустой результат"
            return row
        row["phone"] = f"+{me.phone}" if getattr(me, "phone", None) else None
        return row
    except Exception as exc:
        log.warning("Не удалось прочитать профиль сессии %s: %s", norm, exc)
        row["error"] = str(exc)
        return row
    finally:
        if owned:
            try:
                await client.disconnect()
            except Exception:
                pass


async def list_sessions_info() -> list[dict[str, Any]]:
    """Сканирует SESSIONS_DIR и для каждого `.session` запрашивает телефон."""
    names = scan_sessions_dir()
    out: list[dict[str, Any]] = []
    for name in names:
        out.append(await probe_session_info(name))
    return out
