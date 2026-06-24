"""Проверка входящих запросов по API-ключу (X-API-Key)."""
from __future__ import annotations

from fastapi import Header, HTTPException

from discovery_api.config import get_api_key


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    expected = get_api_key()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Эндпойнт выключен: задайте API_KEY в окружении сервиса",
        )
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=401,
            detail="Нужен заголовок X-API-Key с верным значением",
        )
    return True

