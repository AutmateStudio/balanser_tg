"""PostgreSQL-хелперы для throughput-теста."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from loadtest.prod_e2e.db import ChannelRow, channel_ref_from_row, normalize_ref

log = logging.getLogger("throughput.db")

ACTIVE_STATUSES = ("queued", "scheduled", "retry", "in_progress")
PAUSEABLE_STATUSES = ("queued", "scheduled", "retry")
TERMINAL_STATUSES = ("done", "failed", "cancelled")


@dataclass
class TaskSnapshot:
    id: int
    status: str
    task_type_code: str
    account_id: int | None
    last_error: str | None
    created_at: datetime | None
    finished_at: datetime | None
    channel_ref: str | None


class ThroughputDb:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def accounts_overview(self) -> dict[str, Any]:
        assert self.pool
        async with self.pool.acquire() as conn:
            # VIEW может отсутствовать на старых стендах — fallback на accounts
            try:
                row = await conn.fetchrow(
                    """
                    SELECT pickable_accounts_count, busy_accounts_count,
                           orphan_account_locks
                    FROM v_accounts_overview
                    """
                )
                if row:
                    return {
                        "pickable": int(row["pickable_accounts_count"] or 0),
                        "busy": int(row["busy_accounts_count"] or 0),
                        "orphan": int(row["orphan_account_locks"] or 0),
                        "source": "v_accounts_overview",
                    }
            except asyncpg.UndefinedTableError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("v_accounts_overview недоступен: %s", exc)

            row = await conn.fetchrow(
                """
                SELECT
                  count(*) FILTER (
                    WHERE is_enabled AND status IN ('active','cooldown')
                      AND current_task_id IS NULL
                      AND (cooldown_until IS NULL OR cooldown_until <= now())
                  ) AS pickable,
                  count(*) FILTER (WHERE current_task_id IS NOT NULL) AS busy,
                  count(*) FILTER (
                    WHERE current_task_id IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM task_queue t
                        WHERE t.id = accounts.current_task_id
                          AND t.status = 'in_progress'::task_status
                      )
                  ) AS orphan
                FROM accounts
                """
            )
            return {
                "pickable": int(row["pickable"] or 0),
                "busy": int(row["busy"] or 0),
                "orphan": int(row["orphan"] or 0),
                "source": "accounts",
            }

    async def fetch_candidates(
        self,
        *,
        platform_id: int,
        limit: int,
        exclude_assigned: bool = True,
    ) -> list[ChannelRow]:
        """Каналы из source_channels, предпочтительно без assigned_account_id."""
        assert self.pool
        assigned_clause = (
            "AND (sc.assigned_account_id IS NULL)" if exclude_assigned else ""
        )
        rows = await self.pool.fetch(
            f"""
            SELECT sc.id, sc.external_url, sc.external_channel_id, sc.name, sc.metadata
            FROM source_channels sc
            WHERE sc.platform_id = $1
              {assigned_clause}
              AND (
                    (sc.external_url IS NOT NULL AND sc.external_url ILIKE '%t.me/%')
                 OR (sc.metadata ? 'username')
                 OR (sc.external_channel_id IS NOT NULL AND sc.external_channel_id <> '')
              )
            ORDER BY sc.updated_at DESC NULLS LAST, sc.id DESC
            LIMIT $2
            """,
            platform_id,
            limit,
        )
        out: list[ChannelRow] = []
        seen: set[str] = set()
        for r in rows:
            meta = r["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            ref = channel_ref_from_row(
                r["external_url"], r["external_channel_id"], meta
            )
            n = normalize_ref(ref)
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(
                ChannelRow(
                    id=int(r["id"]),
                    external_url=r["external_url"],
                    external_channel_id=r["external_channel_id"],
                    name=r["name"],
                    ref=ref,
                )
            )
        return out

    async def count_pauseable_tasks(self) -> int:
        assert self.pool
        return int(
            await self.pool.fetchval(
                """
                SELECT count(*) FROM task_queue
                WHERE status = ANY($1::task_status[])
                """,
                list(PAUSEABLE_STATUSES),
            )
            or 0
        )

    async def task_status_counts(self, task_ids: list[int]) -> dict[str, int]:
        assert self.pool
        if not task_ids:
            return {}
        rows = await self.pool.fetch(
            """
            SELECT status::text AS status, count(*)::int AS cnt
            FROM task_queue
            WHERE id = ANY($1::bigint[])
            GROUP BY status
            """,
            task_ids,
        )
        return {str(r["status"]): int(r["cnt"]) for r in rows}

    async def fetch_tasks(self, task_ids: list[int]) -> list[TaskSnapshot]:
        assert self.pool
        if not task_ids:
            return []
        rows = await self.pool.fetch(
            """
            SELECT id, status::text AS status, task_type_code, account_id,
                   last_error, created_at, finished_at,
                   payload->>'channel_ref' AS channel_ref
            FROM task_queue
            WHERE id = ANY($1::bigint[])
            """,
            task_ids,
        )
        return [
            TaskSnapshot(
                id=int(r["id"]),
                status=str(r["status"]),
                task_type_code=str(r["task_type_code"]),
                account_id=int(r["account_id"]) if r["account_id"] is not None else None,
                last_error=r["last_error"],
                created_at=r["created_at"],
                finished_at=r["finished_at"],
                channel_ref=r["channel_ref"],
            )
            for r in rows
        ]

    async def find_task_ids_for_channels(
        self,
        *,
        parser_id: str,
        channel_refs: list[str],
        task_type: str,
        since: datetime | None = None,
    ) -> list[int]:
        """Найти task_id по dedup_key после HTTP enqueue."""
        assert self.pool
        norms = [normalize_ref(r) for r in channel_refs if normalize_ref(r)]
        if not norms:
            return []
        keys = [f"{task_type}:{parser_id}:{n}" for n in norms]
        rows = await self.pool.fetch(
            """
            SELECT id
            FROM task_queue
            WHERE task_type_code = $1
              AND dedup_key = ANY($2::text[])
              AND ($3::timestamptz IS NULL OR created_at >= $3)
            ORDER BY id
            """,
            task_type,
            keys,
            since,
        )
        return [int(r["id"]) for r in rows]

    async def resource_summary_sample(self) -> dict[str, Any]:
        assert self.pool
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                      count(*) FILTER (WHERE available_percent >= 50) AS ok_50,
                      count(*) FILTER (WHERE available_percent < 20) AS low_20,
                      avg(available_percent) AS avg_available,
                      count(*) AS accounts
                    FROM v_account_resource_summary
                    """
                )
                r = rows[0] if rows else None
                if r:
                    return {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "accounts": int(r["accounts"] or 0),
                        "ok_50": int(r["ok_50"] or 0),
                        "low_20": int(r["low_20"] or 0),
                        "avg_available": float(r["avg_available"] or 0),
                        "source": "v_account_resource_summary",
                    }
            except Exception as exc:  # noqa: BLE001
                log.warning("resource summary view: %s", exc)

            cooldown = await conn.fetchval(
                """
                SELECT count(*) FROM accounts
                WHERE cooldown_until IS NOT NULL AND cooldown_until > now()
                """
            )
            return {
                "ts": datetime.now(timezone.utc).isoformat(),
                "cooldown_accounts": int(cooldown or 0),
                "source": "accounts.cooldown",
            }

    async def foreign_tasks_in_window(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        exclude_created_by_prefix: str = "throughput_test:",
    ) -> dict[str, Any]:
        """Задачи, созданные внешними источниками в окне теста."""
        assert self.pool
        until = until or datetime.now(timezone.utc)
        rows = await self.pool.fetch(
            """
            SELECT task_type_code, created_by, count(*)::int AS cnt
            FROM task_queue
            WHERE created_at >= $1 AND created_at < $2
              AND task_type_code IN ('parser_add_channel', 'parser_remove_channel')
              AND COALESCE(created_by, '') NOT LIKE $3
            GROUP BY task_type_code, created_by
            ORDER BY cnt DESC
            """,
            since,
            until,
            f"{exclude_created_by_prefix}%",
        )
        items = [
            {
                "task_type_code": r["task_type_code"],
                "created_by": r["created_by"],
                "count": int(r["cnt"]),
            }
            for r in rows
        ]
        return {
            "total": sum(i["count"] for i in items),
            "by_source": items,
        }

    async def per_account_stats(self, task_ids: list[int]) -> list[dict[str, Any]]:
        assert self.pool
        if not task_ids:
            return []
        rows = await self.pool.fetch(
            """
            SELECT
              t.account_id,
              a.session_name,
              count(*) FILTER (WHERE t.status = 'done')::int AS done,
              count(*) FILTER (WHERE t.status = 'failed')::int AS failed,
              count(*) FILTER (
                WHERE t.status IN ('queued','scheduled','retry','in_progress')
              )::int AS pending,
              count(*)::int AS total,
              avg(
                EXTRACT(EPOCH FROM (t.finished_at - t.created_at))
              ) FILTER (WHERE t.status = 'done' AND t.finished_at IS NOT NULL)
                AS avg_latency_sec
            FROM task_queue t
            LEFT JOIN accounts a ON a.id = t.account_id
            WHERE t.id = ANY($1::bigint[])
            GROUP BY t.account_id, a.session_name
            ORDER BY done DESC NULLS LAST, total DESC
            """,
            task_ids,
        )
        return [
            {
                "account_id": int(r["account_id"]) if r["account_id"] is not None else None,
                "session_name": r["session_name"],
                "done": int(r["done"] or 0),
                "failed": int(r["failed"] or 0),
                "pending": int(r["pending"] or 0),
                "total": int(r["total"] or 0),
                "avg_latency_sec": (
                    float(r["avg_latency_sec"]) if r["avg_latency_sec"] is not None else None
                ),
            }
            for r in rows
        ]

    async def error_breakdown(self, task_ids: list[int], *, limit: int = 20) -> list[dict[str, Any]]:
        assert self.pool
        if not task_ids:
            return []
        rows = await self.pool.fetch(
            """
            SELECT
              COALESCE(NULLIF(trim(last_error), ''), '(empty)') AS err,
              count(*)::int AS cnt
            FROM task_queue
            WHERE id = ANY($1::bigint[])
              AND status IN ('failed', 'retry', 'cancelled')
              AND last_error IS NOT NULL
            GROUP BY 1
            ORDER BY cnt DESC
            LIMIT $2
            """,
            task_ids,
            limit,
        )
        return [{"error": r["err"], "count": int(r["cnt"])} for r in rows]

    async def latency_stats(self, task_ids: list[int]) -> dict[str, Any]:
        assert self.pool
        if not task_ids:
            return {"count": 0}
        row = await self.pool.fetchrow(
            """
            SELECT
              count(*)::int AS cnt,
              avg(EXTRACT(EPOCH FROM (finished_at - created_at))) AS avg_sec,
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (finished_at - created_at))
              ) AS p50_sec,
              percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (finished_at - created_at))
              ) AS p95_sec,
              min(EXTRACT(EPOCH FROM (finished_at - created_at))) AS min_sec,
              max(EXTRACT(EPOCH FROM (finished_at - created_at))) AS max_sec
            FROM task_queue
            WHERE id = ANY($1::bigint[])
              AND status = 'done'
              AND finished_at IS NOT NULL
              AND created_at IS NOT NULL
            """,
            task_ids,
        )
        if not row or not row["cnt"]:
            return {"count": 0}

        def _f(v: Any) -> float | None:
            return float(v) if v is not None else None

        return {
            "count": int(row["cnt"]),
            "avg_sec": _f(row["avg_sec"]),
            "p50_sec": _f(row["p50_sec"]),
            "p95_sec": _f(row["p95_sec"]),
            "min_sec": _f(row["min_sec"]),
            "max_sec": _f(row["max_sec"]),
        }

    async def hourly_done_counts(
        self, task_ids: list[int], *, since: datetime
    ) -> list[dict[str, Any]]:
        assert self.pool
        if not task_ids:
            return []
        rows = await self.pool.fetch(
            """
            SELECT
              date_trunc('hour', finished_at) AS hour,
              count(*)::int AS done
            FROM task_queue
            WHERE id = ANY($1::bigint[])
              AND status = 'done'
              AND finished_at IS NOT NULL
              AND finished_at >= $2
            GROUP BY 1
            ORDER BY 1
            """,
            task_ids,
            since,
        )
        return [
            {
                "hour": r["hour"].isoformat() if r["hour"] else None,
                "done": int(r["done"]),
            }
            for r in rows
        ]
