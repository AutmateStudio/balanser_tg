"""Пейсинг join-операций: минимальный интервал между join на одном аккаунте.

ПРОБЛЕМА (диагностировано нагрузочным тестом 2026-07-22):
    Часовой RPH-governor (см. DB/A22) ограничивает total join за час, но НЕ
    распределяет их во времени. Малый пул живых аккаунтов (в тесте — 3 active)
    успевает выбрать свой часовой лимит за минуты — бурстом. Бурст join
    триггерит Telegram FloodWait → аккаунт уходит в cooldown, пул схлопывается,
    throughput падает с 2400/ч до ~90/ч.

РЕШЕНИЕ:
    Гейт минимального интервала между join на одном аккаунте (в дополнение к
    часовому RPH). Равномерно размазывает join во времени: при интервале 120с
    один аккаунт делает не чаще 1 join / 2 мин ≈ 30 join/час — это совпадает с
    безопасным governor'ом из A22 (30 add/аккаунт/час). На здоровом пуле из ~30
    аккаунтов даёт ~900/час без бурстов; на малом пуле мягко ограничивает темп,
    не гоня аккаунты в FloodWait.

РЕАЛИЗАЦИЯ:
    Per-process (один event loop → один процесс воркеров). Диспетчер держит
    аккаунт зарезервированным (accounts.current_task_id), поэтому по каждому
    account_id одновременно работает не более одного воркера — гонок за слот
    внутри аккаунта нет. ``try_acquire`` синхронный и не содержит ``await``,
    поэтому атомарен в рамках asyncio (между корутинами не прерывается).

    Гейт НЕ блокирует (не спит): если слот ещё не готов, диспетчер отклоняет
    аккаунт (как при нехватке ресурса), пробует следующий, а если весь пул на
    пейсинге — откладывает задачу (postpone) на остаток интервала. Так воркеры
    не залипают на sleep и не держат lease задачи (WORKER_LOCK_TTL_SECONDS).

Управление через env:
- ``JOIN_PACING_SECONDS`` (default 120) — минимальный интервал между join на
  одном аккаунте, в секундах. ``0`` — пейсинг выключен.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("queue_worker.pacing")

_ENV_INTERVAL = "JOIN_PACING_SECONDS"
_DEFAULT_INTERVAL_SECONDS = 120.0


def _resolve_interval() -> float:
    """Интервал пейсинга из env (fallback — безопасные 120с = 30 join/ч)."""
    raw = os.getenv(_ENV_INTERVAL, "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r не число — использую default %.0fs",
            _ENV_INTERVAL,
            raw,
            _DEFAULT_INTERVAL_SECONDS,
        )
        return _DEFAULT_INTERVAL_SECONDS
    if value < 0:
        logger.warning(
            "%s=%s < 0 — пейсинг выключен", _ENV_INTERVAL, value
        )
        return 0.0
    return value


class AccountPacer:
    """Гейт минимального интервала между join на одном аккаунте (per-process)."""

    def __init__(self, interval_seconds: float | None = None) -> None:
        self._interval = (
            _resolve_interval() if interval_seconds is None else float(interval_seconds)
        )
        # account_id → monotonic-время, начиная с которого разрешён следующий join.
        self._next_allowed: dict[int, float] = {}

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def try_acquire(self, account_id: int) -> float:
        """Пытается занять слот join для аккаунта.

        Возвращает ``0.0`` — слот получен (и следующий отодвинут на интервал).
        Возвращает ``> 0`` — сколько секунд осталось ждать (слот НЕ занят).

        Метод синхронный и без ``await`` — атомарен в рамках event loop.
        """
        if self._interval <= 0:
            return 0.0
        now = time.monotonic()
        earliest = self._next_allowed.get(account_id, 0.0)
        if now >= earliest:
            self._next_allowed[account_id] = now + self._interval
            return 0.0
        return earliest - now

    def reset(self, account_id: int | None = None) -> None:
        """Сбрасывает состояние пейсинга (для тестов)."""
        if account_id is None:
            self._next_allowed.clear()
        else:
            self._next_allowed.pop(account_id, None)


_default_pacer: AccountPacer | None = None


def get_pacer() -> AccountPacer:
    """Синглтон пейсера процесса (ленивая инициализация)."""
    global _default_pacer
    if _default_pacer is None:
        _default_pacer = AccountPacer()
        if _default_pacer.enabled:
            logger.info(
                "join pacing включён: интервал=%.0fs (~%.0f join/аккаунт/час)",
                _default_pacer.interval_seconds,
                3600.0 / _default_pacer.interval_seconds,
            )
        else:
            logger.info("join pacing выключен (%s=0)", _ENV_INTERVAL)
    return _default_pacer
