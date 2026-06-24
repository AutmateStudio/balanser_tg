from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from discovery_api.config import get_api_hash, get_api_id


async def activate_session_from_file(
    session_file: str,
    api_id: int | None = None,
    api_hash: str | None = None,
) -> TelegramClient:
    """
    Load and connect an existing Telethon .session file.
    """
    resolved_api_id = int(api_id) if api_id is not None else get_api_id()
    resolved_api_hash = api_hash or get_api_hash()
    client = TelegramClient(session_file, resolved_api_id, resolved_api_hash)
    await client.connect()
    return client


async def session_string_from_file(
    session_name: str,
    api_id: int | None = None,
    api_hash: str | None = None,
) -> str:
    """
    Открывает Telethon `.session`-файл на сервере и возвращает строковое
    представление сессии (StringSession). После чтения клиент отключается,
    чтобы файл не был заблокирован для повторного использования.
    """
    resolved_api_id = int(api_id) if api_id is not None else get_api_id()
    resolved_api_hash = api_hash or get_api_hash()
    client = TelegramClient(session_name, resolved_api_id, resolved_api_hash)
    await client.connect()
    try:
        return StringSession.save(client.session)
    finally:
        await client.disconnect()

