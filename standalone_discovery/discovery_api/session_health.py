"""Модель здоровья Telethon-сессии и классификация ошибок Telegram.

Используется балансировщиком `SessionClump` для мониторинга живости сессий,
детекта бана/блокировки и flood-aware распределения нагрузки.

Health хранится только в памяти (in-memory) и не попадает в persistence.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Tuple

import telethon.errors as te

ErrorKind = Literal["flood", "banned", "transient", "fatal"]

# Текст FloodWait-ошибок из resolve-пути выглядит как "FloodWait 42s ...".
_FLOOD_WAIT_RE = re.compile(r"FloodWait\s+(\d+)\s*s", re.IGNORECASE)


def parse_flood_wait_seconds(message: Optional[str]) -> Optional[int]:
    """Извлекает число секунд из строки ошибки FloodWait.

    `resolve_channel_to_chat_id` гасит `FloodWaitError` в человекочитаемую
    строку вида ``"FloodWait 42s при resolve '...'"``. Чтобы балансировщик мог
    отреагировать (увести каналы с флудящей сессии), извлекаем секунды отсюда.
    Возвращает None, если это не FloodWait.
    """
    if not message:
        return None
    m = _FLOOD_WAIT_RE.search(message)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, TypeError):
        return None


class SessionStatus:
    """Строковые константы статуса сессии (для сериализации в API)."""

    STARTING = "starting"
    HEALTHY = "healthy"
    FLOOD_WAIT = "flood_wait"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


# Ошибки, означающие, что аккаунт заблокирован/сессия отозвана — миграция каналов
# на здоровые сессии и остановка listener'а.
_BANNED_ERRORS: Tuple[type[BaseException], ...] = (
    te.UserDeactivatedBanError,
    te.UserDeactivatedError,
    te.AuthKeyUnregisteredError,
    te.AuthKeyDuplicatedError,
    te.SessionRevokedError,
    te.SessionExpiredError,
    te.PhoneNumberBannedError,
    te.UnauthorizedError,
)

# Временные ошибки сети/сервера — переподключение с backoff.
# `TimeoutError` (builtins) и `asyncio.TimeoutError` различаются на Python 3.10,
# поэтому перечисляем оба.
_TRANSIENT_ERRORS: Tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    te.RpcCallFailError,
    te.ServerError,
    te.TimedOutError,
)


def classify_telethon_error(exc: BaseException) -> Tuple[ErrorKind, Optional[int]]:
    """Классифицирует исключение Telethon/сети.

    Возвращает кортеж `(kind, seconds)`, где `kind`:
    - `flood` — `FloodWaitError`/`FloodError`; `seconds` = время ожидания;
    - `banned` — аккаунт заблокирован/сессия отозвана; `seconds` = None;
    - `transient` — временная ошибка сети/сервера, нужен reconnect; `seconds` = None;
    - `fatal` — всё остальное (неизвестная ошибка); `seconds` = None.

    Проверки идут от частного к общему: `FloodWaitError` имеет `seconds`,
    бан-ошибки проверяются раньше базового `UnauthorizedError`.
    """
    if isinstance(exc, (te.FloodWaitError, te.FloodError)):
        seconds = int(getattr(exc, "seconds", 0) or 0)
        return "flood", seconds
    if isinstance(exc, _BANNED_ERRORS):
        return "banned", None
    if isinstance(exc, _TRANSIENT_ERRORS):
        return "transient", None
    return "fatal", None


@dataclass
class SessionHealth:
    """In-memory снимок здоровья одной Telethon-сессии."""

    status: str = SessionStatus.STARTING
    connected: bool = False
    last_event_at: Optional[float] = None
    last_connected_at: Optional[float] = None
    flood_until: Optional[float] = None
    flood_wait_count: int = 0
    flood_wait_total_seconds: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
    reconnect_count: int = 0
    banned: bool = False
    ban_reason: Optional[str] = None

    def touch_event(self) -> None:
        """Отметить, что сессия только что получила сообщение/апдейт."""
        self.last_event_at = time.time()

    def mark_connected(self) -> None:
        self.connected = True
        self.last_connected_at = time.time()
        # Не перетираем banned/flood_wait статусы успешным коннектом supervisor'а.
        if self.status in (SessionStatus.STARTING, SessionStatus.DISCONNECTED):
            self.status = SessionStatus.HEALTHY

    def mark_disconnected(self) -> None:
        self.connected = False
        if not self.banned:
            self.status = SessionStatus.DISCONNECTED

    def mark_flood(self, seconds: int) -> None:
        secs = max(0, int(seconds or 0))
        self.flood_until = time.time() + secs
        self.flood_wait_count += 1
        self.flood_wait_total_seconds += secs
        self.status = SessionStatus.FLOOD_WAIT

    def clear_flood_if_expired(self) -> bool:
        """Снимает истёкший flood_wait. Возвращает True, если флуд был снят."""
        if self.flood_until is None:
            return False
        if time.time() >= self.flood_until:
            self.flood_until = None
            if not self.banned and self.status == SessionStatus.FLOOD_WAIT:
                self.status = (
                    SessionStatus.HEALTHY if self.connected else SessionStatus.DISCONNECTED
                )
            return True
        return False

    def mark_banned(self, reason: str) -> None:
        self.banned = True
        self.ban_reason = reason
        self.status = SessionStatus.BANNED
        self.connected = False

    def record_error(self, message: str) -> None:
        self.error_count += 1
        self.last_error = message
        self.last_error_at = time.time()

    def record_reconnect(self) -> None:
        self.reconnect_count += 1

    def in_flood(self) -> bool:
        return self.flood_until is not None and time.time() < self.flood_until

    def is_available(self) -> bool:
        """Доступна ли сессия для приёма новых каналов (балансировка)."""
        if self.banned:
            return False
        if self.status == SessionStatus.DISCONNECTED:
            return False
        return not self.in_flood()

    def to_dict(self) -> dict[str, Any]:
        flood_remaining: Optional[int] = None
        if self.flood_until is not None:
            flood_remaining = max(0, int(self.flood_until - time.time()))
        return {
            "status": self.status,
            "connected": self.connected,
            "last_event_at": self.last_event_at,
            "last_connected_at": self.last_connected_at,
            "flood_until": self.flood_until,
            "flood_remaining_seconds": flood_remaining,
            "flood_wait_count": self.flood_wait_count,
            "flood_wait_total_seconds": self.flood_wait_total_seconds,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "reconnect_count": self.reconnect_count,
            "banned": self.banned,
            "ban_reason": self.ban_reason,
        }
