"""G4-доп. — self-healing: убивает зависшие 'idle in transaction' сессии этого приложения.

Инцидент 2026-08-11: при пересоздании контейнера (`docker compose up -d`,
SIGTERM → grace period → возможный SIGKILL) до 20 in-process worker'ов
discovery-api могут обрывать соединение к PG посреди открытой транзакции
(`pick_and_reserve`/`_RESERVE_SQL`/`_MARK_STUCK_TIMED_OUT_SQL` и т.п.), и
Postgres не всегда сразу замечает разрыв TCP — сессия остаётся висеть
`idle in transaction`, удерживая блокировки строк `accounts`/`task_queue`,
пока не истечёт TCP keepalive (может быть часами). Наблюдалось: 9-10 сессий
одновременно застряли на >13 минут, заблокировав watchdog (`_MARK_STUCK_
TIMED_OUT_SQL`) и обычные reserve-вызовы — ручное `pg_terminate_backend`
единственный способ снять блокировку.

Скоуп: только сессии ТЕКУЩЕГО db-пользователя приложения (current_user) —
не трогаем чужие подключения к той же БД (напр. gleb_admin из n8n).
Легитимная транзакция в этом приложении никогда не остаётся открытой
дольше секунд — порог в минутах безопасен с большим запасом.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app_balance.queue import db

log = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

_REAP_SQL = """
SELECT pid, now() - xact_start AS xact_age, left(query, 200) AS last_query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND usename = current_user
  AND pid <> pg_backend_pid()
  AND xact_start < now() - ($1::int * interval '1 second')
"""


@dataclass(frozen=True, slots=True)
class IdleTransactionReaperConfig:
    enabled: bool = True
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls) -> "IdleTransactionReaperConfig":
        return cls(
            enabled=_env_flag("IDLE_TRANSACTION_REAPER_ENABLED", True),
            max_age_seconds=_env_int(
                "IDLE_TRANSACTION_REAPER_MAX_AGE_SECONDS", 300
            ),
        )


async def reap_idle_transactions(max_age_seconds: int) -> list[int]:
    """Убивает 'idle in transaction' сессии этого пользователя старше порога.

    Возвращает список pid, которые были завершены (для лога/метрики тика).
    """
    async with db.acquire() as conn:
        rows = await conn.fetch(_REAP_SQL, max_age_seconds)
        reaped: list[int] = []
        for row in rows:
            pid = int(row["pid"])
            try:
                await conn.execute("SELECT pg_terminate_backend($1)", pid)
            except Exception:  # noqa: BLE001 — одна неудачная попытка не должна ронять тик
                log.warning(
                    "idle_transaction_reaper: не удалось завершить pid=%s",
                    pid,
                    exc_info=True,
                )
                continue
            log.warning(
                "idle_transaction_reaper: завершена зависшая транзакция "
                "pid=%s возраст=%s последний запрос=%r",
                pid,
                row["xact_age"],
                row["last_query"],
            )
            reaped.append(pid)
        return reaped
