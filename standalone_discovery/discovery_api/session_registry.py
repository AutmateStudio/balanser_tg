"""Process-wide реестр Telethon-клиентов по `session_name`.

Один подключённый `TelegramClient` на путь к `.session`-файлу + кеш
`StringSession`-строки. Устраняет `sqlite3.OperationalError: database is locked`
при параллельных HTTP-запросах и при одновременной работе парсера и
`/add-channel-by-link` на одной сессии.

SessionClump — оркестратор пула Parser_client (шард по Telegram-аккаунтам).
Parser_client — один слушатель на сессию.

Дисконнект всех клиентов — только `release_all()` (обычно на shutdown FastAPI).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

from discovery_api.config import (
    get_api_hash,
    get_api_id,
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
from discovery_api.session_health import (
    SessionHealth,
    SessionStatus,
    classify_telethon_error,
    parse_flood_wait_seconds,
)

log = logging.getLogger(__name__)

_clients: dict[str, TelegramClient] = {}
_session_strings: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}
_clumps: dict[str, "SessionClump"] = {}
_health_monitor_task: asyncio.Task[None] | None = None

# F3: однократный warning о выключении idle-rebalance при активном PG-балансере.
_warned_rebalance_disabled = False


class ChannelQuotaExceeded(Exception):
    """Все сессии в clump достигли лимита каналов."""


class NoHealthySessionError(Exception):
    """Нет ни одной здоровой сессии для приёма каналов."""


# Имена настраиваемых параметров clump (для валидации и сериализации).
_CLUMP_CONFIG_FIELDS = (
    "max_channels_per_session",
    "max_reconnects",
    "reconnect_backoff_base",
    "reconnect_backoff_max",
    "flood_migrate_threshold_seconds",
    "resolve_min_interval",
    "auto_migrate",
    "add_channels_per_hour",
    "rebalance_enabled",
    "rebalance_idle_start_hour",
    "rebalance_idle_end_hour",
    "rebalance_high_watermark_ratio",
    "rebalance_low_watermark_ratio",
    "rebalance_min_gap_channels",
    "rebalance_max_moves_per_tick",
    "rebalance_cooldown_hours",
)


@dataclass
class ClumpConfig:
    """Параметры балансировки/антифлуда конкретного clump.

    Каждое поле — необязательное переопределение: если `None`, берётся живое
    значение из env (`config.py`). Так per-clump настройки сосуществуют с
    глобальными дефолтами и переключением через окружение.
    """

    max_channels_per_session: Optional[int] = None
    max_reconnects: Optional[int] = None
    reconnect_backoff_base: Optional[float] = None
    reconnect_backoff_max: Optional[float] = None
    flood_migrate_threshold_seconds: Optional[int] = None
    resolve_min_interval: Optional[float] = None
    auto_migrate: Optional[bool] = None
    add_channels_per_hour: Optional[int] = None
    rebalance_enabled: Optional[bool] = None
    rebalance_idle_start_hour: Optional[int] = None
    rebalance_idle_end_hour: Optional[int] = None
    rebalance_high_watermark_ratio: Optional[float] = None
    rebalance_low_watermark_ratio: Optional[float] = None
    rebalance_min_gap_channels: Optional[int] = None
    rebalance_max_moves_per_tick: Optional[int] = None
    rebalance_cooldown_hours: Optional[float] = None

    def eff_max_channels_per_session(self) -> int:
        if self.max_channels_per_session is not None:
            return self.max_channels_per_session
        return get_max_channels_per_session()

    def eff_max_reconnects(self) -> int:
        if self.max_reconnects is not None:
            return self.max_reconnects
        return get_session_max_reconnects()

    def eff_reconnect_backoff_base(self) -> float:
        if self.reconnect_backoff_base is not None:
            return self.reconnect_backoff_base
        return get_session_reconnect_backoff_base()

    def eff_reconnect_backoff_max(self) -> float:
        if self.reconnect_backoff_max is not None:
            return self.reconnect_backoff_max
        return get_session_reconnect_backoff_max()

    def eff_flood_migrate_threshold_seconds(self) -> int:
        if self.flood_migrate_threshold_seconds is not None:
            return self.flood_migrate_threshold_seconds
        return get_session_flood_migrate_threshold_seconds()

    def eff_resolve_min_interval(self) -> float:
        if self.resolve_min_interval is not None:
            return self.resolve_min_interval
        return get_session_resolve_min_interval()

    def eff_auto_migrate(self) -> bool:
        if self.auto_migrate is not None:
            return self.auto_migrate
        return get_session_auto_migrate()

    def eff_add_channels_per_hour(self) -> int:
        if self.add_channels_per_hour is not None:
            return self.add_channels_per_hour
        return get_add_channels_per_hour()

    def eff_rebalance_enabled(self) -> bool:
        # F3: при активном PG-балансере (F2) старый idle-rebalance принудительно
        # выключен, чтобы два механизма переноса каналов не конфликтовали.
        if get_use_pg_queue():
            global _warned_rebalance_disabled
            if not _warned_rebalance_disabled:
                log.warning(
                    "idle-rebalance отключён: активен PG-балансер (USE_PG_QUEUE=true, F2)"
                )
                _warned_rebalance_disabled = True
            return False
        if self.rebalance_enabled is not None:
            return self.rebalance_enabled
        return get_rebalance_enabled()

    def eff_rebalance_idle_start_hour(self) -> int:
        if self.rebalance_idle_start_hour is not None:
            return self.rebalance_idle_start_hour
        return get_rebalance_idle_start_hour()

    def eff_rebalance_idle_end_hour(self) -> int:
        if self.rebalance_idle_end_hour is not None:
            return self.rebalance_idle_end_hour
        return get_rebalance_idle_end_hour()

    def eff_rebalance_high_watermark_ratio(self) -> float:
        if self.rebalance_high_watermark_ratio is not None:
            return self.rebalance_high_watermark_ratio
        return get_rebalance_high_watermark_ratio()

    def eff_rebalance_low_watermark_ratio(self) -> float:
        if self.rebalance_low_watermark_ratio is not None:
            return self.rebalance_low_watermark_ratio
        return get_rebalance_low_watermark_ratio()

    def eff_rebalance_min_gap_channels(self) -> int:
        if self.rebalance_min_gap_channels is not None:
            return self.rebalance_min_gap_channels
        return get_rebalance_min_gap_channels()

    def eff_rebalance_max_moves_per_tick(self) -> int:
        if self.rebalance_max_moves_per_tick is not None:
            return self.rebalance_max_moves_per_tick
        return get_rebalance_max_moves_per_tick()

    def eff_rebalance_cooldown_hours(self) -> float:
        if self.rebalance_cooldown_hours is not None:
            return self.rebalance_cooldown_hours
        return get_rebalance_cooldown_hours()

    def overrides(self) -> dict[str, Any]:
        """Только явно заданные переопределения (для persistence)."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def apply(self, **kwargs: Any) -> list[str]:
        """Применяет переданные (не-None) переопределения. Возвращает изменённые ключи."""
        changed: list[str] = []
        for name in _CLUMP_CONFIG_FIELDS:
            if name in kwargs and kwargs[name] is not None:
                setattr(self, name, kwargs[name])
                changed.append(name)
        return changed

    def to_dict(self) -> dict[str, Any]:
        """Эффективные значения (override или env) + список переопределённых."""
        return {
            "max_channels_per_session": self.eff_max_channels_per_session(),
            "max_reconnects": self.eff_max_reconnects(),
            "reconnect_backoff_base": self.eff_reconnect_backoff_base(),
            "reconnect_backoff_max": self.eff_reconnect_backoff_max(),
            "flood_migrate_threshold_seconds": self.eff_flood_migrate_threshold_seconds(),
            "resolve_min_interval": self.eff_resolve_min_interval(),
            "auto_migrate": self.eff_auto_migrate(),
            "add_channels_per_hour": self.eff_add_channels_per_hour(),
            "rebalance_enabled": self.eff_rebalance_enabled(),
            "rebalance_idle_start_hour": self.eff_rebalance_idle_start_hour(),
            "rebalance_idle_end_hour": self.eff_rebalance_idle_end_hour(),
            "rebalance_high_watermark_ratio": self.eff_rebalance_high_watermark_ratio(),
            "rebalance_low_watermark_ratio": self.eff_rebalance_low_watermark_ratio(),
            "rebalance_min_gap_channels": self.eff_rebalance_min_gap_channels(),
            "rebalance_max_moves_per_tick": self.eff_rebalance_max_moves_per_tick(),
            "rebalance_cooldown_hours": self.eff_rebalance_cooldown_hours(),
            "overridden": sorted(self.overrides().keys()),
        }


def _lock_for(session_name: str) -> asyncio.Lock:
    return _locks.setdefault(session_name, asyncio.Lock())


def reset_for_tests() -> None:
    """Очищает реестр. Только для юнит-тестов."""
    global _health_monitor_task
    _clients.clear()
    _session_strings.clear()
    _locks.clear()
    _clumps.clear()
    _health_monitor_task = None
    try:
        from discovery_api.account_store import reset_account_db_for_tests
        from discovery_api.action_queue import reset_action_queue_for_tests

        reset_account_db_for_tests()
        reset_action_queue_for_tests()
    except Exception:
        pass


_UNAUTHORIZED_MSG = (
    "Сессия '{session_name}' не авторизована; войдите в аккаунт для этой session"
)


def _unauthorized_message(session_name: str) -> str:
    return _UNAUTHORIZED_MSG.format(session_name=session_name)


def find_parser_client(session_name: str) -> Optional["Parser_client"]:
    """Parser_client в загруженных clump'ах (для обновления in-memory health)."""
    for clump in _clumps.values():
        pc = clump._session_index.get(session_name)
        if pc is not None:
            return pc
    return None


async def get_or_create_client(session_name: str) -> TelegramClient:
    """Возвращает единственный подключённый клиент для данного `session_name`."""
    async with _lock_for(session_name):
        client = _clients.get(session_name)
        if client is not None:
            if not client.is_connected():
                await client.connect()
            if not await client.is_user_authorized():
                msg = _unauthorized_message(session_name)
                await notify_session_unauthorized(session_name, msg)
                raise RuntimeError(msg)
            return client

        client = TelegramClient(session_name, int(get_api_id()), get_api_hash())
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            msg = _unauthorized_message(session_name)
            await notify_session_unauthorized(session_name, msg)
            raise RuntimeError(msg)
        _clients[session_name] = client
        log.info("Telethon-клиент подключён и зарегистрирован: %s", session_name)
        return client


async def get_session_string(session_name: str) -> str:
    """Строка `StringSession` для данного `session_name` (кешируется в памяти)."""
    cached = _session_strings.get(session_name)
    if cached is not None:
        return cached
    client = await get_or_create_client(session_name)
    s = StringSession.save(client.session)
    _session_strings[session_name] = s
    return s


async def is_session_active(session_name: str) -> bool:
    """True, если для сессии уже есть подключённый и авторизованный клиент."""
    client = _clients.get(session_name)
    if client is None:
        return False
    try:
        return bool(client.is_connected() and await client.is_user_authorized())
    except Exception:
        return False


async def release_client(session_name: str) -> None:
    """Отключает и удаляет клиент из process-wide реестра."""
    async with _lock_for(session_name):
        client = _clients.pop(session_name, None)
        _session_strings.pop(session_name, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


def get_clump(parser_id: str) -> Optional["SessionClump"]:
    return _clumps.get(parser_id)


def iter_clumps() -> list[tuple[str, "SessionClump"]]:
    """F2: снимок (parser_id, clump) всех загруженных clump'ов реестра."""
    return list(_clumps.items())


async def get_or_create_clump(
    parser_id: str,
    session_names: list[str],
    webhook_url: str,
    *,
    clump_name: Optional[str] = None,
) -> "SessionClump":
    existing = _clumps.get(parser_id)
    if existing is not None:
        return existing
    clump = SessionClump(
        session_name_list=list(session_names),
        clump_name=clump_name or parser_id,
        webhook_url=webhook_url,
    )
    _clumps[parser_id] = clump
    return clump


async def remove_clump(parser_id: str) -> None:
    clump = _clumps.pop(parser_id, None)
    if clump is not None:
        await clump.stop()


async def release_all() -> None:
    """Останавливает clump'ы, отключает всех клиентов и очищает реестр."""
    await stop_health_monitor()
    for pid in list(_clumps.keys()):
        try:
            await remove_clump(pid)
        except Exception:
            log.exception("Ошибка stop clump при release_all: %s", pid)
    _clumps.clear()

    for name, client in list(_clients.items()):
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            log.exception("Ошибка disconnect при release_all: %s", name)
    _clients.clear()
    _session_strings.clear()
    _locks.clear()
    log.info("session_registry: все клиенты отключены")


async def _health_check_once() -> None:
    """Один тик HealthMonitor: обновить health всех сессий и добить миграции."""
    for clump in list(_clumps.values()):
        for pc in list(clump.parser_client_list):
            health = pc.health
            health.clear_flood_if_expired()
            if health.banned:
                continue
            if health.status == SessionStatus.ERROR:
                continue
            client = _clients.get(pc.session_name)
            if client is None:
                continue
            try:
                connected = bool(client.is_connected())
                authorized = connected and await client.is_user_authorized()
            except Exception:
                connected = False
                authorized = False
            if connected and authorized:
                if not health.in_flood():
                    health.mark_connected()
            else:
                health.mark_disconnected()
        # Добиваем осиротевшие каналы, если появились здоровые сессии.
        if clump.pending_channels and clump.config.eff_auto_migrate():
            try:
                await clump.retry_pending_channels()
            except Exception:
                log.exception(
                    "Ошибка повторного размещения pending-каналов clump %s",
                    clump.clump_name,
                )
        if clump.config.eff_rebalance_enabled():
            try:
                await clump.rebalance_idle()
            except Exception:
                log.exception(
                    "Ошибка idle-rebalance clump %s", clump.clump_name
                )


async def _health_monitor_loop() -> None:
    interval = get_session_health_check_interval()
    while True:
        try:
            await asyncio.sleep(interval)
            await _health_check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле HealthMonitor")


def start_health_monitor() -> None:
    """Запускает глобальный HealthMonitor (идемпотентно)."""
    global _health_monitor_task
    if _health_monitor_task is not None and not _health_monitor_task.done():
        return
    try:
        _health_monitor_task = asyncio.create_task(
            _health_monitor_loop(), name="session-health-monitor"
        )
        log.info("HealthMonitor запущен")
    except RuntimeError:
        # Нет работающего event loop (например, при некоторых тестах) — пропускаем.
        log.debug("HealthMonitor не запущен: нет активного event loop")


async def stop_health_monitor() -> None:
    global _health_monitor_task
    task = _health_monitor_task
    _health_monitor_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


OnDownCallback = Callable[[str, str], Awaitable[None]]


async def _persist_flood_cooldown_pg(session_name: str, seconds: int) -> None:
    """D6: FloodWait → PG accounts.cooldown_until (no-op без QUEUE_DATABASE_URL)."""
    try:
        from app_balance.queue.account_health_sync import persist_flood_cooldown
    except ImportError:
        return
    await persist_flood_cooldown(session_name, seconds)


async def _persist_banned_pg(session_name: str, reason: str) -> None:
    """D6: ban → PG accounts.status (no-op без QUEUE_DATABASE_URL)."""
    try:
        from app_balance.queue.account_health_sync import persist_banned
    except ImportError:
        return
    await persist_banned(session_name, reason)


async def _persist_unauthorized_pg(session_name: str, reason: str) -> None:
    """D6: неавторизованная сессия → PG accounts.status=error."""
    try:
        from app_balance.queue.account_health_sync import persist_unauthorized
    except ImportError:
        return
    await persist_unauthorized(session_name, reason)


async def notify_session_unauthorized(session_name: str, message: str) -> None:
    """In-memory health clump + PG accounts для неавторизованной сессии."""
    pc = find_parser_client(session_name)
    if pc is not None:
        pc.health.mark_unauthorized(message)
    await _persist_unauthorized_pg(session_name, message)


class Parser_client:
    """Один Telethon-аккаунт: resolve каналов и listener на allowed_chat_ids.

    Listener управляется supervisor-loop'ом: при разрыве соединения он
    переподключается с экспоненциальным backoff, на FloodWait — ждёт, на
    бане/фатальной ошибке — помечает health и вызывает `on_down`-колбэк clump'а.
    """

    def __init__(
        self,
        session_name: str,
        *,
        on_down: Optional[OnDownCallback] = None,
        config: Optional[ClumpConfig] = None,
    ) -> None:
        self.session_name = session_name
        self.channels: list[str] = []
        self.allowed_chat_ids: set[int] = set()
        self.ref_to_chat_id: dict[str, int] = {}
        self._webhook_url: str = ""
        self._supervisor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stop_requested = False
        self._on_down = on_down
        self._config = config or ClumpConfig()
        self._last_resolve_at: float = 0.0
        self._add_timestamps: Deque[float] = deque()
        self.health = SessionHealth()

    def _prune_add_timestamps(self) -> None:
        cutoff = time.time() - 3600.0
        while self._add_timestamps and self._add_timestamps[0] < cutoff:
            self._add_timestamps.popleft()

    def can_accept_add(self, hourly_limit: int) -> bool:
        if hourly_limit <= 0:
            return True
        self._prune_add_timestamps()
        return len(self._add_timestamps) < hourly_limit

    def record_channel_add(self) -> None:
        self._add_timestamps.append(time.time())

    async def get_client(self) -> TelegramClient:
        return await get_or_create_client(self.session_name)

    def is_running(self) -> bool:
        return self._supervisor_task is not None and not self._supervisor_task.done()

    def info(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "channels": list(self.channels),
            "allowed_chat_ids": sorted(int(x) for x in self.allowed_chat_ids),
            "running": self.is_running(),
            "channel_count": len(self.channels),
            "health": self.health.to_dict(),
        }

    async def _trigger_down(self, reason: str) -> None:
        if self._on_down is None:
            return
        try:
            await self._on_down(self.session_name, reason)
        except Exception:
            log.exception(
                "on_down callback бросил исключение для сессии %s", self.session_name
            )

    async def _supervise(self, webhook_url: str) -> None:
        """Цикл: подключиться → слушать → на ошибке reconnect/flood/ban."""
        from discovery_api.parser_functions import run_session_listener

        consecutive_failures = 0
        while not self._stop_requested:
            try:
                client = await get_or_create_client(self.session_name)
                self.health.mark_connected()
                consecutive_failures = 0
                await run_session_listener(
                    session_name=self.session_name,
                    webhook_url=webhook_url,
                    allowed_chat_ids=self.allowed_chat_ids,
                    pending_refs=[],
                    client=client,
                    on_event=self.health.touch_event,
                )
                if self._stop_requested:
                    break
                # run_until_disconnected вернулся без исключения — обычный разрыв.
                self.health.mark_disconnected()
                consecutive_failures += 1
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - классифицируем ниже
                kind, seconds = classify_telethon_error(exc)
                self.health.record_error(f"{type(exc).__name__}: {exc}")
                log.warning(
                    "Listener сессии %s упал (%s): %s",
                    self.session_name,
                    kind,
                    exc,
                )
                if kind == "flood":
                    secs = int(seconds or 0)
                    self.health.mark_flood(secs)
                    await _persist_flood_cooldown_pg(self.session_name, secs)
                    threshold = self._config.eff_flood_migrate_threshold_seconds()
                    if secs >= threshold and self._config.eff_auto_migrate():
                        await self._trigger_down(f"flood_wait {secs}s")
                    await self._sleep_interruptible(secs)
                    consecutive_failures = 0
                    continue
                if kind == "banned":
                    self.health.mark_banned(str(exc))
                    await _persist_banned_pg(self.session_name, str(exc))
                    await self._trigger_down(f"banned: {exc}")
                    break
                if kind == "unauthorized":
                    msg = str(exc)
                    self.health.mark_unauthorized(msg)
                    await _persist_unauthorized_pg(self.session_name, msg)
                    await self._trigger_down(f"unauthorized: {exc}")
                    break
                # transient / fatal — наращиваем счётчик и идём в backoff
                self.health.mark_disconnected()
                consecutive_failures += 1

            if self._stop_requested:
                break
            if consecutive_failures >= self._config.eff_max_reconnects():
                self.health.status = SessionStatus.DISCONNECTED
                await self._trigger_down(
                    f"disconnected после {consecutive_failures} попыток"
                )
                break
            backoff = min(
                self._config.eff_reconnect_backoff_base()
                * (2 ** max(0, consecutive_failures - 1)),
                self._config.eff_reconnect_backoff_max(),
            )
            self.health.record_reconnect()
            await self._sleep_interruptible(backoff)

    async def _sleep_interruptible(self, seconds: float) -> None:
        """Сон, прерываемый при запросе остановки (квантами по 1 сек)."""
        remaining = float(max(0.0, seconds))
        while remaining > 0 and not self._stop_requested:
            chunk = min(1.0, remaining)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def start(self, webhook_url: str) -> None:
        async with self._lock:
            self._webhook_url = webhook_url
            if self.is_running():
                return
            self._stop_requested = False
            if self.health.status in (SessionStatus.DISCONNECTED,):
                self.health.status = SessionStatus.STARTING
            self._supervisor_task = asyncio.create_task(
                self._supervise(webhook_url),
                name=f"parser-supervisor-{self.session_name}",
            )

    async def stop(self) -> None:
        async with self._lock:
            self._stop_requested = True
            task = self._supervisor_task
            self._supervisor_task = None
            if task is None or task.done():
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _respect_resolve_pacing(self) -> None:
        """Гарантирует минимальный интервал между resolve-RPC на одну сессию."""
        min_interval = self._config.eff_resolve_min_interval()
        if min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_resolve_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_resolve_at = time.monotonic()

    async def add_channel(
        self, raw: str, *, webhook_url: Optional[str] = None
    ) -> tuple[Optional[int], Optional[str]]:
        from discovery_api.parser_functions import resolve_channel_to_chat_id

        wh = (webhook_url or self._webhook_url or "").strip()
        client = await self.get_client()
        await self._respect_resolve_pacing()
        chat_id, err = await resolve_channel_to_chat_id(client, raw)
        if err or chat_id is None:
            # FloodWait при resolve гасится в строку — поднимаем его в health,
            # чтобы балансировщик увёл следующие каналы на другую сессию.
            flood_secs = parse_flood_wait_seconds(err)
            if flood_secs is not None:
                self.health.mark_flood(flood_secs)
                await _persist_flood_cooldown_pg(self.session_name, flood_secs)
            return None, err

        cid = int(chat_id)
        if cid in self.allowed_chat_ids:
            return cid, None

        self.allowed_chat_ids.add(cid)
        self.ref_to_chat_id[raw] = cid
        if raw not in self.channels:
            self.channels.append(raw)
            self.record_channel_add()

        if wh and not self.is_running():
            await self.start(wh)
        log.info(
            "parser add_channel OK session=%s ref=%s chat_id=%s listener_running=%s",
            self.session_name,
            raw,
            cid,
            self.is_running(),
        )
        return cid, None

    async def remove_channel(self, raw: str) -> bool:
        from discovery_api.parser_functions import resolve_channel_to_chat_id

        cid = self.ref_to_chat_id.get(raw)
        if cid is None:
            client = await self.get_client()
            resolved, err = await resolve_channel_to_chat_id(client, raw)
            if err or resolved is None:
                return False
            cid = int(resolved)

        if cid not in self.allowed_chat_ids:
            return False

        self.allowed_chat_ids.discard(cid)
        self.ref_to_chat_id.pop(raw, None)
        self.channels[:] = [c for c in self.channels if c != raw]
        return True

    def restore_channel(
        self, raw: str, listen_peer_id: int, *, webhook_url: str = ""
    ) -> None:
        """Восстановление из JSON без resolve (startup)."""
        cid = int(listen_peer_id)
        self.allowed_chat_ids.add(cid)
        self.ref_to_chat_id[raw] = cid
        if raw not in self.channels:
            self.channels.append(raw)
        if webhook_url:
            self._webhook_url = webhook_url


class SessionClump:
    """Групповые операции над пулом Parser_client: каналы, start/stop слушателей."""

    def __init__(
        self,
        session_name_list: list[str],
        clump_name: str,
        webhook_url: str = "",
    ) -> None:
        if not session_name_list:
            raise ValueError("session_name_list не может быть пустым")
        self.session_name_list = list(session_name_list)
        self.clump_name = clump_name
        self.webhook_url = webhook_url
        self.assignments: dict[str, str] = {}
        self.pending_channels: list[str] = []
        self.config = ClumpConfig()
        # Редактируемые метаданные аккаунтов: session_name -> {display_name, description}
        self.account_meta: dict[str, dict[str, str]] = {}
        self._channel_rebalance_at: dict[str, float] = {}
        self._migrate_lock = asyncio.Lock()
        self.parser_client_list: list[Parser_client] = [
            self._make_client(name) for name in self.session_name_list
        ]
        self._session_index: dict[str, Parser_client] = {
            pc.session_name: pc for pc in self.parser_client_list
        }

    def _make_client(self, session_name: str) -> Parser_client:
        return Parser_client(
            session_name, on_down=self._on_session_down, config=self.config
        )

    def update_config(self, **overrides: Any) -> dict[str, Any]:
        """Применяет per-clump переопределения параметров балансировщика.

        Изменения подхватываются на лету (supervisor и `_pick_target` читают
        config при каждом обращении). Возвращает актуальный снимок конфига.
        """
        changed = self.config.apply(**overrides)
        snapshot = self.config.to_dict()
        snapshot["changed"] = changed
        return snapshot

    def has_session(self, session_name: str) -> bool:
        return session_name in self._session_index

    @staticmethod
    def _default_display_name(session_name: str) -> str:
        base = session_name.replace("\\", "/").rsplit("/", 1)[-1]
        if base.endswith(".session"):
            base = base[: -len(".session")]
        return base or session_name

    def get_account_meta(self, session_name: str) -> dict[str, str]:
        meta = self.account_meta.get(session_name) or {}
        return {
            "display_name": meta.get("display_name")
            or self._default_display_name(session_name),
            "description": meta.get("description") or "",
        }

    def set_account_meta(
        self,
        session_name: str,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, str]:
        """Обновляет название/описание аккаунта (только переданные поля)."""
        if not self.has_session(session_name):
            raise ValueError(f"Аккаунт не найден в clump: {session_name}")
        cur = dict(self.account_meta.get(session_name) or {})
        if display_name is not None:
            cur["display_name"] = display_name
        if description is not None:
            cur["description"] = description
        self.account_meta[session_name] = cur
        return self.get_account_meta(session_name)

    def account_summary(self, pc: Parser_client) -> dict[str, Any]:
        """Краткая строка для таблицы аккаунтов: статус, блокировки, кол-во каналов."""
        h = pc.health
        flood_remaining: Optional[int] = None
        if h.flood_until is not None:
            flood_remaining = max(0, int(h.flood_until - time.time()))
        meta = self.get_account_meta(pc.session_name)
        return {
            "session_name": pc.session_name,
            "display_name": meta["display_name"],
            "clump_name": self.clump_name,
            "status": h.status,
            "banned": h.banned,
            "ban_reason": h.ban_reason,
            "last_error": h.last_error,
            "flood_remaining_seconds": flood_remaining,
            "connected": h.connected,
            "running": pc.is_running(),
            "channel_count": len(pc.channels),
            "max_channels_per_session": self.config.eff_max_channels_per_session(),
        }

    def list_account_summaries(self) -> list[dict[str, Any]]:
        return [self.account_summary(pc) for pc in self.parser_client_list]

    def account_detail(self, session_name: str) -> Optional[dict[str, Any]]:
        """Полная карточка аккаунта для формы редактирования."""
        pc = self._session_index.get(session_name)
        if pc is None:
            return None
        meta = self.get_account_meta(session_name)
        return {
            "session_name": pc.session_name,
            "display_name": meta["display_name"],
            "description": meta["description"],
            "clump_name": self.clump_name,
            "running": pc.is_running(),
            "channel_count": len(pc.channels),
            "limits": self.config.to_dict(),
            "health": pc.health.to_dict(),
        }

    def account_channels(self, session_name: str) -> Optional[list[str]]:
        pc = self._session_index.get(session_name)
        if pc is None:
            return None
        return list(pc.channels)

    def _pc_available(self, pc: Parser_client) -> bool:
        from discovery_api.account_registry import is_admin_blocked

        if is_admin_blocked(pc.session_name):
            return False
        if not pc.health.is_available():
            return False
        hourly = self.config.eff_add_channels_per_hour()
        if not pc.can_accept_add(hourly):
            return False
        return True

    def _eff_channel_limit(self, pc: Parser_client) -> int:
        from discovery_api.account_registry import eff_channel_limit_info

        limit, _src = eff_channel_limit_info(
            pc.session_name, self.config.eff_max_channels_per_session()
        )
        return limit

    def _pick_min_load(self) -> Parser_client:
        """Совместимость со старым API: делегирует во flood/health-aware выбор."""
        return self._pick_target()

    def _pick_target(self) -> Parser_client:
        """Выбирает здоровую сессию с минимальной загрузкой каналами.

        Исключаются забаненные, отключённые, admin-blocked, hourly-quota
        и находящиеся в активном FloodWait сессии.
        """
        available = [pc for pc in self.parser_client_list if self._pc_available(pc)]
        if not available:
            raise NoHealthySessionError(
                "Нет здоровых сессий в clump (бан/дисконнект/flood/block/quota)"
            )
        min_count = 10**9
        chosen: Parser_client | None = None
        for pc in available:
            count = len(pc.channels)
            limit = self._eff_channel_limit(pc)
            if count < limit and count < min_count:
                min_count = count
                chosen = pc
        if chosen is None:
            raise ChannelQuotaExceeded(
                "Достигнут лимит каналов на всех доступных аккаунтах в clump"
            )
        return chosen

    def _find_owner(self, raw: str) -> Optional[Parser_client]:
        session_name = self.assignments.get(raw)
        if session_name:
            return self._session_index.get(session_name)
        for pc in self.parser_client_list:
            if raw in pc.channels:
                return pc
        return None

    def list_channels(self) -> list[str]:
        out: list[str] = []
        for pc in self.parser_client_list:
            for ch in pc.channels:
                if ch not in out:
                    out.append(ch)
        return out

    def all_allowed_chat_ids(self) -> set[int]:
        result: set[int] = set()
        for pc in self.parser_client_list:
            result |= pc.allowed_chat_ids
        return result

    def health_summary(self) -> dict[str, Any]:
        total = len(self.parser_client_list)
        healthy = sum(
            1
            for pc in self.parser_client_list
            if pc.health.status == SessionStatus.HEALTHY
        )
        banned = [
            pc.session_name
            for pc in self.parser_client_list
            if pc.health.banned
        ]
        flood = [
            pc.session_name
            for pc in self.parser_client_list
            if pc.health.in_flood()
        ]
        disconnected = [
            pc.session_name
            for pc in self.parser_client_list
            if pc.health.status == SessionStatus.DISCONNECTED
        ]
        return {
            "total": total,
            "healthy": healthy,
            "banned": banned,
            "flood": flood,
            "disconnected": disconnected,
            "pending_channels": list(self.pending_channels),
        }

    def info(self) -> dict[str, Any]:
        return {
            "clump_name": self.clump_name,
            "session_name_list": list(self.session_name_list),
            "webhook_url": self.webhook_url,
            "channel_list": self.list_channels(),
            "assignments": dict(self.assignments),
            "allowed_chat_ids": sorted(self.all_allowed_chat_ids()),
            "per_session": [pc.info() for pc in self.parser_client_list],
            "running": any(pc.is_running() for pc in self.parser_client_list),
            "health_summary": self.health_summary(),
            "config": self.config.to_dict(),
        }

    async def start(self) -> None:
        for pc in self.parser_client_list:
            if pc.channels:
                await pc.start(self.webhook_url)

    async def stop(self) -> None:
        for pc in self.parser_client_list:
            await pc.stop()

    async def add_channel_on_session(
        self,
        session_name: str,
        raw: str,
        *,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """D1: добавление канала на конкретную сессию (воркер уже выбрал аккаунт).

        Обходит `_pick_target`. Сессия задаётся явно (PG reserve / B6); канал
        не уходит в `pending_channels`. Если канал уже слушается на другой
        сессии clump'а — ошибка (перенос — D2 `move_channel`).
        """
        ref = (raw or "").strip()
        if not ref:
            return {
                "channel": raw,
                "session_name": None,
                "chat_id": None,
                "error": "Пустое значение канала",
            }

        pc = self._session_index.get(session_name)
        if pc is None:
            return {
                "channel": ref,
                "session_name": session_name,
                "chat_id": None,
                "error": f"Сессия не найдена в clump: {session_name}",
            }

        wh = (webhook_url or self.webhook_url or "").strip() or None

        owner = self._find_owner(ref)
        if owner is not None:
            if owner.session_name != session_name:
                return {
                    "channel": ref,
                    "session_name": session_name,
                    "chat_id": None,
                    "error": (
                        f"Канал уже на другой сессии: {owner.session_name}"
                    ),
                }
            if ref in owner.channels:
                return {
                    "channel": ref,
                    "session_name": session_name,
                    "chat_id": owner.ref_to_chat_id.get(ref),
                    "error": None,
                    "already_present": True,
                }

        existing_ids = self.all_allowed_chat_ids()
        chat_id, err = await pc.add_channel(ref, webhook_url=wh)
        already_present = False
        if chat_id is not None and err is None:
            if int(chat_id) in existing_ids:
                already_present = True
                log.info(
                    "clump add_channel_on_session OK parser=%s session=%s ref=%s chat_id=%s already_present=true",
                    self.clump_name,
                    session_name,
                    ref,
                    chat_id,
                )
                return {
                    "channel": ref,
                    "session_name": session_name,
                    "chat_id": int(chat_id),
                    "error": None,
                    "already_present": True,
                }
            self.assignments[ref] = session_name
            log.info(
                "clump add_channel_on_session OK parser=%s session=%s ref=%s chat_id=%s already_present=false",
                self.clump_name,
                session_name,
                ref,
                chat_id,
            )

        return {
            "channel": ref,
            "session_name": session_name,
            "chat_id": chat_id,
            "error": err,
            "already_present": already_present,
        }

    @staticmethod
    def _strip_channel_local(pc: Parser_client, ref: str) -> None:
        """Снимает канал с сессии локально (без Telethon LeaveChannel)."""
        cid = pc.ref_to_chat_id.get(ref)
        if cid is not None:
            pc.allowed_chat_ids.discard(int(cid))
        pc.ref_to_chat_id.pop(ref, None)
        pc.channels[:] = [c for c in pc.channels if c != ref]

    async def move_channel(
        self,
        ref: str,
        from_session: str,
        to_session: str,
        *,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """D2: перенос одного канала между конкретными сессиями clump'а.

        Идемпотентнее batch ``migrate_channels``: source и target заданы явно
        (PG move_channel / C4 dual reserve). Повторный вызов безопасен, если
        канал уже на ``to_session``.
        """
        channel_ref = (ref or "").strip()
        if not channel_ref:
            return {
                "channel": ref,
                "from_session": from_session,
                "to_session": to_session,
                "session_name": None,
                "chat_id": None,
                "error": "Пустое значение канала",
            }

        source = self._session_index.get(from_session)
        target = self._session_index.get(to_session)
        if source is None:
            return {
                "channel": channel_ref,
                "from_session": from_session,
                "to_session": to_session,
                "session_name": None,
                "chat_id": None,
                "error": f"Исходная сессия не найдена в clump: {from_session}",
            }
        if target is None:
            return {
                "channel": channel_ref,
                "from_session": from_session,
                "to_session": to_session,
                "session_name": None,
                "chat_id": None,
                "error": f"Целевая сессия не найдена в clump: {to_session}",
            }

        owner = self._find_owner(channel_ref)
        if owner is not None and owner.session_name == to_session:
            if channel_ref in owner.channels:
                return {
                    "channel": channel_ref,
                    "from_session": from_session,
                    "to_session": to_session,
                    "session_name": to_session,
                    "chat_id": owner.ref_to_chat_id.get(channel_ref),
                    "error": None,
                    "already_present": True,
                    "moved": False,
                }
        if owner is not None and owner.session_name not in (
            from_session,
            to_session,
        ):
            return {
                "channel": channel_ref,
                "from_session": from_session,
                "to_session": to_session,
                "session_name": None,
                "chat_id": None,
                "error": (
                    f"Канал на неожиданной сессии: {owner.session_name}"
                ),
            }

        if channel_ref in source.channels or self.assignments.get(
            channel_ref
        ) == from_session:
            self._strip_channel_local(source, channel_ref)
            if self.assignments.get(channel_ref) == from_session:
                self.assignments.pop(channel_ref, None)

        wh = (webhook_url or self.webhook_url or "").strip() or None
        chat_id, err = await target.add_channel(channel_ref, webhook_url=wh)
        if chat_id is not None and err is None:
            self.assignments[channel_ref] = to_session

        return {
            "channel": channel_ref,
            "from_session": from_session,
            "to_session": to_session,
            "session_name": to_session,
            "chat_id": chat_id,
            "error": err,
            "already_present": False,
            "moved": chat_id is not None and err is None,
        }

    async def add_channel(self, raw: str) -> dict[str, Any]:
        ref = (raw or "").strip()
        if not ref:
            return {
                "channel": raw,
                "session_name": None,
                "chat_id": None,
                "error": "Пустое значение канала",
            }

        existing_ids = self.all_allowed_chat_ids()

        owner = self._find_owner(ref)
        if owner is not None:
            if ref in owner.channels:
                return {
                    "channel": ref,
                    "session_name": owner.session_name,
                    "chat_id": owner.ref_to_chat_id.get(ref),
                    "error": None,
                    "already_present": True,
                }
            chat_id, err = await owner.add_channel(ref, webhook_url=self.webhook_url)
            return {
                "channel": ref,
                "session_name": owner.session_name,
                "chat_id": chat_id,
                "error": err,
                "already_present": False,
            }

        try:
            pc = self._pick_target()
        except (ChannelQuotaExceeded, NoHealthySessionError) as e:
            # Нет ёмкости/здоровых сессий прямо сейчас — не падаем, а откладываем
            # канал в очередь; HealthMonitor доразместит его, когда появится
            # свободная здоровая сессия.
            self._enqueue_pending(ref)
            return {
                "channel": ref,
                "session_name": None,
                "chat_id": None,
                "error": str(e),
                "already_present": False,
                "deferred": True,
            }
        chat_id, err = await pc.add_channel(ref, webhook_url=self.webhook_url)
        if chat_id is not None and err is None:
            if int(chat_id) in existing_ids:
                return {
                    "channel": ref,
                    "session_name": pc.session_name,
                    "chat_id": int(chat_id),
                    "error": None,
                    "already_present": True,
                }
            self.assignments[ref] = pc.session_name
        return {
            "channel": ref,
            "session_name": pc.session_name,
            "chat_id": chat_id,
            "error": err,
            "already_present": False,
        }

    async def remove_channel(self, raw: str) -> bool:
        ref = (raw or "").strip()
        owner = self._find_owner(ref)
        if owner is None:
            return False
        removed = await owner.remove_channel(ref)
        if removed:
            self.assignments.pop(ref, None)
        return removed

    def _enqueue_pending(self, ref: str, bucket: Optional[list[str]] = None) -> None:
        """Кладёт канал в очередь повторной обработки (без дублей)."""
        if not ref:
            return
        if ref not in self.pending_channels:
            self.pending_channels.append(ref)
        if bucket is not None and ref not in bucket:
            bucket.append(ref)

    async def add_channels_batch(
        self, refs: list[str]
    ) -> dict[str, Any]:
        """Массовое добавление каналов, устойчивое к экстремальному объёму.

        Батч НЕ прерывается на нехватке ёмкости/здоровья: каналы, которые не
        удалось разместить сейчас (квота, нет здоровых сессий, FloodWait),
        складываются в `pending_channels` и будут размещены позже на тике
        HealthMonitor (`retry_pending_channels`). Постоянные ошибки resolve
        (нет доступа/нет обсуждения) просто попадают в `errors`.
        """
        added: list[str] = []
        already: list[str] = []
        errors: list[str] = []
        pending: list[str] = []
        batch_assignments: dict[str, str] = {}

        for raw in refs:
            ref = (raw or "").strip()
            try:
                result = await self.add_channel(raw)
            except (ChannelQuotaExceeded, NoHealthySessionError) as e:
                # Защитный путь: штатно add_channel не бросает эти исключения
                # (откладывает канал), но на случай переопределения — ловим.
                self._enqueue_pending(ref, pending)
                errors.append(f"{raw}: {e!s}")
                continue

            err = result.get("error")
            if result.get("deferred"):
                # Канал уже в self.pending_channels — отражаем это в ответе.
                if ref and ref not in pending:
                    pending.append(ref)
                if err:
                    errors.append(f"{raw}: {err}")
                continue
            if err:
                # FloodWait — временная ошибка, ставим канал в очередь повторной
                # попытки. Прочие ошибки resolve считаем постоянными.
                if parse_flood_wait_seconds(err) is not None:
                    self._enqueue_pending(ref, pending)
                errors.append(f"{raw}: {err}")
                continue
            if result.get("already_present"):
                already.append(raw)
            else:
                added.append(raw)
            sn = result.get("session_name")
            if sn:
                batch_assignments[raw] = str(sn)

        return {
            "channel_list": self.list_channels(),
            "added": added,
            "already_present": already,
            "errors": errors,
            "pending": pending,
            "assignments": {**self.assignments, **batch_assignments},
        }

    async def remove_channels_batch(
        self, refs: list[str]
    ) -> dict[str, Any]:
        removed: list[str] = []
        not_found: list[str] = []
        errors: list[str] = []

        for raw in refs:
            ref = (raw or "").strip()
            if not ref:
                errors.append("Пустое значение канала")
                continue
            if await self.remove_channel(ref):
                removed.append(raw)
            else:
                not_found.append(raw)

        return {
            "channel_list": self.list_channels(),
            "removed": removed,
            "not_found": not_found,
            "errors": errors,
        }

    async def _on_session_down(self, session_name: str, reason: str) -> None:
        """Колбэк из supervisor'а Parser_client при бане/долгом флуде/дисконнекте."""
        if not self.config.eff_auto_migrate():
            log.warning(
                "Сессия %s недоступна (%s), авто-миграция выключена",
                session_name,
                reason,
            )
            return
        try:
            await self.migrate_channels(session_name, reason)
        except Exception:
            log.exception(
                "Ошибка авто-миграции каналов с сессии %s", session_name
            )

    async def migrate_channels(self, from_session: str, reason: str) -> dict[str, Any]:
        """Переносит каналы упавшей сессии на здоровые сессии clump'а.

        Для каждого канала повторно выполняется resolve/join на сессии-приёмнике.
        Каналы, для которых здоровых сессий не нашлось, попадают в
        `pending_channels` и будут обработаны на следующем тике HealthMonitor.
        """
        async with self._migrate_lock:
            source = self._session_index.get(from_session)
            if source is None:
                return {"migrated": [], "pending": [], "errors": []}

            to_move = list(source.channels)
            migrated: list[str] = []
            errors: list[str] = []

            log.warning(
                "Миграция %d каналов с сессии %s (причина: %s)",
                len(to_move),
                from_session,
                reason,
            )

            for ref in to_move:
                # Снимаем канал с упавшей сессии (локально, без RPC).
                source.allowed_chat_ids.discard(source.ref_to_chat_id.get(ref, 0))
                source.ref_to_chat_id.pop(ref, None)
                source.channels[:] = [c for c in source.channels if c != ref]
                self.assignments.pop(ref, None)

                try:
                    target = self._pick_target_excluding(from_session)
                except (NoHealthySessionError, ChannelQuotaExceeded) as e:
                    if ref not in self.pending_channels:
                        self.pending_channels.append(ref)
                    errors.append(f"{ref}: {e!s}")
                    continue

                chat_id, err = await target.add_channel(
                    ref, webhook_url=self.webhook_url
                )
                if err or chat_id is None:
                    if ref not in self.pending_channels:
                        self.pending_channels.append(ref)
                    errors.append(f"{ref}: {err}")
                    continue
                self.assignments[ref] = target.session_name
                migrated.append(ref)
                if not target.is_running():
                    await target.start(self.webhook_url)

            self._persist_safe()
            return {"migrated": migrated, "pending": list(self.pending_channels), "errors": errors}

    def _pick_target_excluding(self, exclude_session: str) -> Parser_client:
        """Как `_pick_target`, но исключает конкретную (упавшую) сессию."""
        available = [
            pc
            for pc in self.parser_client_list
            if pc.session_name != exclude_session and self._pc_available(pc)
        ]
        if not available:
            raise NoHealthySessionError(
                "Нет других здоровых сессий для миграции каналов"
            )
        chosen = min(available, key=lambda pc: len(pc.channels))
        if len(chosen.channels) >= self._eff_channel_limit(chosen):
            raise ChannelQuotaExceeded(
                "Все доступные сессии-приёмники достигли лимита каналов"
            )
        return chosen

    async def retry_pending_channels(self) -> dict[str, Any]:
        """Повторная попытка разместить «осиротевшие» каналы на здоровых сессиях."""
        if not self.pending_channels:
            return {"migrated": [], "pending": [], "errors": []}
        async with self._migrate_lock:
            still_pending: list[str] = []
            migrated: list[str] = []
            errors: list[str] = []
            for ref in list(self.pending_channels):
                try:
                    target = self._pick_target()
                except (NoHealthySessionError, ChannelQuotaExceeded) as e:
                    still_pending.append(ref)
                    errors.append(f"{ref}: {e!s}")
                    continue
                chat_id, err = await target.add_channel(
                    ref, webhook_url=self.webhook_url
                )
                if err or chat_id is None:
                    still_pending.append(ref)
                    errors.append(f"{ref}: {err}")
                    continue
                self.assignments[ref] = target.session_name
                migrated.append(ref)
                if not target.is_running():
                    await target.start(self.webhook_url)
            self.pending_channels = still_pending
            if migrated:
                self._persist_safe()
            return {"migrated": migrated, "pending": list(self.pending_channels), "errors": errors}

    def _in_idle_window(self) -> bool:
        start = self.config.eff_rebalance_idle_start_hour()
        end = self.config.eff_rebalance_idle_end_hour()
        hour = datetime.now(timezone.utc).hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def _channel_rebalance_allowed(self, ref: str) -> bool:
        last = self._channel_rebalance_at.get(ref)
        if last is None:
            return True
        cooldown = self.config.eff_rebalance_cooldown_hours() * 3600.0
        return (time.time() - last) >= cooldown

    async def rebalance_idle(self) -> dict[str, Any]:
        """Перенос каналов с перегруженных на свободные сессии в тихое окно."""
        if not self.config.eff_rebalance_enabled() or not self._in_idle_window():
            return {"moved": [], "skipped": "outside_idle_window"}

        loads = [
            (pc, len(pc.channels), self._eff_channel_limit(pc))
            for pc in self.parser_client_list
            if self._pc_available(pc) or pc.health.is_available()
        ]
        if len(loads) < 2:
            return {"moved": [], "skipped": "not_enough_sessions"}

        counts = [c for _pc, c, _lim in loads]
        max_load = max(counts)
        min_load = min(counts)
        min_gap = self.config.eff_rebalance_min_gap_channels()
        if max_load - min_load < min_gap:
            return {"moved": [], "skipped": "gap_too_small"}

        high_ratio = self.config.eff_rebalance_high_watermark_ratio()
        low_ratio = self.config.eff_rebalance_low_watermark_ratio()
        overloaded = [
            pc for pc, count, lim in loads if count >= int(lim * high_ratio)
        ]
        underloaded = [
            pc for pc, count, lim in loads if count <= int(lim * low_ratio)
        ]
        if not overloaded or not underloaded:
            return {"moved": [], "skipped": "no_over_under_pair"}

        max_moves = self.config.eff_rebalance_max_moves_per_tick()
        moved: list[str] = []
        async with self._migrate_lock:
            for source in overloaded:
                if len(moved) >= max_moves:
                    break
                if not source.channels:
                    continue
                ref = source.channels[-1]
                if not self._channel_rebalance_allowed(ref):
                    continue
                try:
                    target = min(
                        underloaded,
                        key=lambda pc: len(pc.channels),
                    )
                    if len(target.channels) >= self._eff_channel_limit(target):
                        continue
                    cid = source.ref_to_chat_id.get(ref)
                    if cid is None:
                        continue
                    source.allowed_chat_ids.discard(cid)
                    source.ref_to_chat_id.pop(ref, None)
                    source.channels[:] = [c for c in source.channels if c != ref]
                    self.assignments.pop(ref, None)
                    chat_id, err = await target.add_channel(
                        ref, webhook_url=self.webhook_url
                    )
                    if err or chat_id is None:
                        source.restore_channel(ref, int(cid), webhook_url=self.webhook_url)
                        self.assignments[ref] = source.session_name
                        continue
                    self.assignments[ref] = target.session_name
                    self._channel_rebalance_at[ref] = time.time()
                    moved.append(ref)
                    if not target.is_running():
                        await target.start(self.webhook_url)
                except Exception as exc:
                    log.warning("rebalance_idle error ref=%s: %s", ref, exc)
        if moved:
            self._persist_safe()
        return {"moved": moved, "skipped": None}

    async def remove_session_force(
        self, session_name: str, *, migrate: bool = True
    ) -> dict[str, Any]:
        """Удаляет сессию из clump, опционально мигрируя каналы."""
        pc = self._session_index.get(session_name)
        if pc is None:
            return {"removed": False, "migrated": []}
        if pc.channels and migrate and self.config.eff_auto_migrate():
            await self.migrate_channels(session_name, "force_remove")
        if pc.channels:
            if migrate:
                raise ValueError(
                    f"На сессии осталось {len(pc.channels)} каналов после миграции"
                )
            raise ValueError(
                f"Нельзя удалить сессию с {len(pc.channels)} каналами без migrate"
            )
        await pc.stop()
        self.parser_client_list.remove(pc)
        self.session_name_list.remove(session_name)
        del self._session_index[session_name]
        self.account_meta.pop(session_name, None)
        to_drop = [k for k, v in self.assignments.items() if v == session_name]
        for k in to_drop:
            del self.assignments[k]
        await release_client(session_name)
        self._persist_safe()
        return {"removed": True, "migrated": []}

    def _persist_safe(self) -> None:
        """Перезаписывает persistence для clump'а (best-effort, без исключений)."""
        parser_id = None
        for pid, clump in _clumps.items():
            if clump is self:
                parser_id = pid
                break
        if parser_id is None:
            return
        try:
            from discovery_api.parser_store import clump_to_record, upsert_job

            upsert_job(clump_to_record(self, parser_id=parser_id))
        except Exception:
            log.debug("Не удалось перезаписать persistence clump'а", exc_info=True)

    async def add_session(self, session_name: str) -> None:
        if session_name in self._session_index:
            return
        pc = self._make_client(session_name)
        self.parser_client_list.append(pc)
        self.session_name_list.append(session_name)
        self._session_index[session_name] = pc

    async def remove_session(self, session_name: str) -> None:
        pc = self._session_index.get(session_name)
        if pc is None:
            raise ValueError(f"Сессия не найдена в clump: {session_name}")
        if pc.channels:
            raise ValueError(
                f"Нельзя удалить сессию с каналами ({len(pc.channels)} шт.)"
            )
        await pc.stop()
        self.parser_client_list.remove(pc)
        self.session_name_list.remove(session_name)
        del self._session_index[session_name]
        self.account_meta.pop(session_name, None)
        to_drop = [k for k, v in self.assignments.items() if v == session_name]
        for k in to_drop:
            del self.assignments[k]

    def restore_from_record(self, record: dict[str, Any]) -> None:
        """Восстановление каналов и assignments из JSON без resolve."""
        wh = str(record.get("webhook_url") or self.webhook_url)
        self.webhook_url = wh

        cfg = record.get("config")
        if isinstance(cfg, dict) and cfg:
            self.config.apply(**cfg)

        meta = record.get("account_meta")
        if isinstance(meta, dict):
            clean: dict[str, dict[str, str]] = {}
            for k, v in meta.items():
                if isinstance(v, dict) and self.has_session(str(k)):
                    clean[str(k)] = {
                        "display_name": str(v.get("display_name") or ""),
                        "description": str(v.get("description") or ""),
                    }
            self.account_meta = clean

        assignments = record.get("assignments")
        if isinstance(assignments, dict):
            self.assignments = {str(k): str(v) for k, v in assignments.items()}

        channel_list = record.get("channel_list")
        if not isinstance(channel_list, list):
            channel_list = []

        allowed_raw = record.get("allowed_chat_ids")
        allowed_set: set[int] = set()
        if isinstance(allowed_raw, list):
            allowed_set = {int(x) for x in allowed_raw}

        # Legacy: один session_name в записи
        legacy_session = record.get("session_name")
        if legacy_session and str(legacy_session) in self._session_index:
            default_pc = self._session_index[str(legacy_session)]
        else:
            default_pc = self.parser_client_list[0]

        chat_ids_iter = iter(sorted(allowed_set))
        for raw in channel_list:
            ref = str(raw)
            session_name = self.assignments.get(ref)
            pc = self._session_index.get(session_name) if session_name else default_pc
            if pc is None:
                pc = default_pc
            cid = pc.ref_to_chat_id.get(ref)
            if cid is None:
                try:
                    numeric = int(ref)
                    if numeric in allowed_set:
                        cid = numeric
                except ValueError:
                    pass
            if cid is None:
                try:
                    cid = next(chat_ids_iter)
                except StopIteration:
                    continue
            pc.restore_channel(ref, int(cid), webhook_url=wh)
            if ref not in self.assignments:
                self.assignments[ref] = pc.session_name

        # Legacy: allowed_chat_ids могут содержать id без отдельной записи в channel_list
        if allowed_set:
            orphan = allowed_set - self.all_allowed_chat_ids()
            if orphan:
                default_pc.allowed_chat_ids |= orphan
