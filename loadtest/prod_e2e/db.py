"""Работа с PostgreSQL (seed, verify, stats)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import PROJECT_PREFIX, CREATED_BY_ADD, CREATED_BY_REMOVE

log = logging.getLogger("loadtest.db")


def normalize_ref(raw: str) -> str:
    """Упрощённая нормализация как в producer._normalize_channel_ref."""
    s = (raw or "").strip()
    if not s:
        return ""
    lowered = s.lower()
    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
        "t.me/",
        "telegram.me/",
    ):
        if lowered.startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.split("?", 1)[0].split("#", 1)[0].strip("/")
    # post path: username/123 → username
    if "/" in s and not s.startswith("+") and not s.startswith("c/"):
        s = s.split("/", 1)[0]
    return s.lstrip("@").strip()


def channel_ref_from_row(external_url: str | None, external_channel_id: str | None, metadata: Any) -> str:
    """Строит channel_ref для add-channels."""
    if isinstance(metadata, dict):
        uname = metadata.get("username")
        if isinstance(uname, str) and uname.strip():
            return "@" + uname.strip().lstrip("@")
    if external_url:
        n = normalize_ref(external_url)
        if n:
            if n.startswith("+") or n.startswith("-") or n.isdigit():
                return n
            return "@" + n
    if external_channel_id:
        return str(external_channel_id).strip()
    return ""


@dataclass
class ChannelRow:
    id: int
    external_url: str | None
    external_channel_id: str | None
    name: str | None
    ref: str


class Db:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def fetch_channel_pool(
        self, *, platform_id: int, limit: int
    ) -> list[ChannelRow]:
        assert self.pool
        # Предпочитаем каналы с username / t.me URL и недавней активностью
        rows = await self.pool.fetch(
            """
            SELECT sc.id, sc.external_url, sc.external_channel_id, sc.name, sc.metadata
            FROM source_channels sc
            WHERE sc.platform_id = $1
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
        seen_refs: set[str] = set()
        for r in rows:
            meta = r["metadata"]
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            ref = channel_ref_from_row(r["external_url"], r["external_channel_id"], meta)
            n = normalize_ref(ref)
            if not n or n in seen_refs:
                continue
            seen_refs.add(n)
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

    async def ensure_projects(
        self,
        *,
        owner_user_id: int,
        run_id: str,
        count: int,
    ) -> list[dict[str, Any]]:
        """Создаёт/переиспользует LOADTEST-{run_id}-{i} проекты."""
        assert self.pool
        projects: list[dict[str, Any]] = []
        async with self.pool.acquire() as conn:
            for i in range(count):
                name = f"{PROJECT_PREFIX}{run_id}-{i:02d}"
                row = await conn.fetchrow(
                    """
                    SELECT id, name, status, owner_user_id
                    FROM monitoring_projects
                    WHERE name = $1 AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    name,
                )
                if row:
                    await conn.execute(
                        """
                        UPDATE monitoring_projects
                        SET status = 'active'::project_status,
                            archived_at = NULL,
                            updated_at = now()
                        WHERE id = $1
                        """,
                        row["id"],
                    )
                    projects.append(
                        {
                            "id": int(row["id"]),
                            "name": name,
                            "index": i,
                            "reused": True,
                        }
                    )
                    continue
                new_id = await conn.fetchval(
                    """
                    INSERT INTO monitoring_projects (
                        owner_user_id, name, description, status,
                        lead_search_prompt, outreach_message_template
                    )
                    VALUES (
                        $1, $2, $3, 'active'::project_status,
                        $4, $5
                    )
                    RETURNING id
                    """,
                    owner_user_id,
                    name,
                    f"Loadtest project {i} run {run_id}",
                    "LOADTEST: qualify leads from group messages",
                    "LOADTEST outreach",
                )
                projects.append(
                    {"id": int(new_id), "name": name, "index": i, "reused": False}
                )
        return projects

    async def link_channels(
        self,
        *,
        project_id: int,
        channel_ids: list[int],
        enabled: bool = True,
    ) -> int:
        assert self.pool
        if not channel_ids:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO project_source_channels (
                    monitoring_project_id, source_channel_id, is_enabled
                )
                VALUES ($1, $2, $3)
                ON CONFLICT (monitoring_project_id, source_channel_id)
                DO UPDATE SET is_enabled = EXCLUDED.is_enabled, updated_at = now()
                """,
                [(project_id, cid, enabled) for cid in channel_ids],
            )
        return len(channel_ids)

    async def set_links_enabled(
        self, *, project_id: int, channel_ids: list[int], enabled: bool
    ) -> int:
        assert self.pool
        if not channel_ids:
            return 0
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE project_source_channels
                SET is_enabled = $3, updated_at = now()
                WHERE monitoring_project_id = $1
                  AND source_channel_id = ANY($2::bigint[])
                """,
                project_id,
                channel_ids,
                enabled,
            )
        # "UPDATE N"
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    async def deactivate_loadtest_projects(self, run_id: str) -> int:
        assert self.pool
        pattern = f"{PROJECT_PREFIX}{run_id}-%"
        async with self.pool.acquire() as conn:
            # выключить связи
            await conn.execute(
                """
                UPDATE project_source_channels psc
                SET is_enabled = false, updated_at = now()
                FROM monitoring_projects mp
                WHERE mp.id = psc.monitoring_project_id
                  AND mp.name LIKE $1
                """,
                pattern,
            )
            result = await conn.execute(
                """
                UPDATE monitoring_projects
                SET status = 'archived'::project_status,
                    archived_at = now(),
                    updated_at = now()
                WHERE name LIKE $1 AND deleted_at IS NULL
                """,
                pattern,
            )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    async def cancel_tasks_for_refs(
        self,
        *,
        parser_id: str,
        refs: list[str],
        task_types: list[str] | None = None,
    ) -> int:
        """Soft-cancel активных задач по dedup_key для parser_id + refs."""
        assert self.pool
        types = task_types or ["parser_add_channel", "parser_remove_channel"]
        norms = [normalize_ref(r) for r in refs if normalize_ref(r)]
        if not norms:
            return 0
        keys: list[str] = []
        for t in types:
            for n in norms:
                keys.append(f"{t}:{parser_id}:{n}")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE task_queue
                SET status = 'cancelled'::task_status,
                    last_error = COALESCE(last_error, '') ||
                        CASE WHEN COALESCE(last_error, '') = '' THEN '' ELSE '; ' END ||
                        'ops:loadtest_cleanup',
                    last_error_at = now(),
                    finished_at = COALESCE(finished_at, now()),
                    locked_by = NULL,
                    locked_at = NULL,
                    locked_until = NULL,
                    updated_at = now()
                WHERE dedup_key = ANY($1::text[])
                  AND status IN (
                        'queued'::task_status,
                        'scheduled'::task_status,
                        'retry'::task_status,
                        'stuck'::task_status,
                        'in_progress'::task_status
                      )
                RETURNING id
                """,
                keys,
            )
            ids = [int(r["id"]) for r in rows]
            if ids:
                await conn.execute(
                    """
                    UPDATE accounts
                    SET current_task_id = NULL, updated_at = now()
                    WHERE current_task_id = ANY($1::bigint[])
                    """,
                    ids,
                )
            return len(ids)

    async def verify_enqueue(
        self,
        *,
        parser_id: str,
        expected_refs: list[str],
        since: datetime,
        task_type: str = "parser_add_channel",
    ) -> dict[str, Any]:
        assert self.pool
        norms = sorted({normalize_ref(r) for r in expected_refs if normalize_ref(r)})
        keys = [f"{task_type}:{parser_id}:{n}" for n in norms]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, status, dedup_key, last_error, created_at, finished_at,
                       payload->>'channel_ref' AS channel_ref
                FROM task_queue
                WHERE task_type_code = $1
                  AND created_at >= $2
                  AND (
                        dedup_key = ANY($3::text[])
                     OR (payload->>'parser_id' = $4
                         AND created_by = $5)
                  )
                """,
                task_type,
                since,
                keys,
                parser_id,
                CREATED_BY_ADD if task_type == "parser_add_channel" else CREATED_BY_REMOVE,
            )
        by_norm: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            key = r["dedup_key"] or ""
            parts = key.split(":")
            n = parts[-1] if len(parts) >= 3 else normalize_ref(r["channel_ref"] or "")
            by_norm.setdefault(n, []).append(dict(r))
        found = set(by_norm.keys())
        expected_set = set(norms)
        missing = sorted(expected_set - found)
        present = sorted(expected_set & found)
        status_counts: dict[str, int] = {}
        for r in rows:
            st = r["status"]
            status_counts[st] = status_counts.get(st, 0) + 1
        return {
            "expected": len(expected_set),
            "found_tasks": len(rows),
            "present_refs": len(present),
            "missing_refs": missing,
            "missing_count": len(missing),
            "completeness": (len(present) / len(expected_set)) if expected_set else 1.0,
            "status_counts": status_counts,
            "since": since.isoformat(),
        }

    async def add_speed_snapshot(
        self, *, parser_id: str, since: datetime
    ) -> dict[str, Any]:
        assert self.pool
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  count(*) FILTER (
                    WHERE status IN ('queued','scheduled','retry','in_progress')
                  ) AS backlog,
                  count(*) FILTER (
                    WHERE status = 'done' AND finished_at > now() - interval '1 minute'
                  ) AS done_1m,
                  count(*) FILTER (
                    WHERE status = 'done' AND finished_at > now() - interval '5 minutes'
                  ) AS done_5m,
                  count(*) FILTER (
                    WHERE status = 'done' AND finished_at >= $2
                  ) AS done_since_start,
                  count(*) FILTER (
                    WHERE last_error ILIKE 'insufficient_resource%'
                      AND updated_at > now() - interval '5 minutes'
                  ) AS insuff_5m,
                  extract(epoch FROM (
                    now() - min(created_at) FILTER (
                      WHERE status IN ('queued','scheduled','retry')
                    )
                  )) AS oldest_queued_age_sec
                FROM task_queue
                WHERE task_type_code = 'parser_add_channel'
                  AND payload->>'parser_id' = $1
                  AND created_at >= $2
                """,
                parser_id,
                since,
            )
            pick = await conn.fetchrow(
                """
                SELECT
                  count(*) FILTER (
                    WHERE is_enabled AND status IN ('active','cooldown')
                      AND current_task_id IS NULL
                      AND (cooldown_until IS NULL OR cooldown_until <= now())
                  ) AS pickable,
                  count(*) FILTER (WHERE current_task_id IS NOT NULL) AS busy
                FROM accounts
                """
            )
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "backlog": int(row["backlog"] or 0),
            "done_1m": int(row["done_1m"] or 0),
            "done_5m": int(row["done_5m"] or 0),
            "done_since_start": int(row["done_since_start"] or 0),
            "insuff_5m": int(row["insuff_5m"] or 0),
            "oldest_queued_age_sec": float(row["oldest_queued_age_sec"] or 0),
            "pickable": int(pick["pickable"] or 0),
            "busy": int(pick["busy"] or 0),
        }

    async def change_task_latency(
        self, *, parser_id: str, since: datetime, task_type: str
    ) -> dict[str, Any]:
        assert self.pool
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  count(*) AS total,
                  count(*) FILTER (WHERE status = 'done') AS done,
                  count(*) FILTER (
                    WHERE status IN ('queued','scheduled','retry','in_progress')
                  ) AS active,
                  avg(extract(epoch FROM (finished_at - created_at)))
                    FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
                    AS avg_apply_sec
                FROM task_queue
                WHERE task_type_code = $1
                  AND payload->>'parser_id' = $2
                  AND created_at >= $3
                """,
                task_type,
                parser_id,
                since,
            )
            p50 = await conn.fetchval(
                """
                SELECT percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY extract(epoch FROM (finished_at - created_at))
                )
                FROM task_queue
                WHERE task_type_code = $1
                  AND payload->>'parser_id' = $2
                  AND created_at >= $3
                  AND status = 'done'
                  AND finished_at IS NOT NULL
                """,
                task_type,
                parser_id,
                since,
            )
            p95 = await conn.fetchval(
                """
                SELECT percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY extract(epoch FROM (finished_at - created_at))
                )
                FROM task_queue
                WHERE task_type_code = $1
                  AND payload->>'parser_id' = $2
                  AND created_at >= $3
                  AND status = 'done'
                  AND finished_at IS NOT NULL
                """,
                task_type,
                parser_id,
                since,
            )
        return {
            "task_type": task_type,
            "total": int(row["total"] or 0),
            "done": int(row["done"] or 0),
            "active": int(row["active"] or 0),
            "avg_apply_sec": float(row["avg_apply_sec"] or 0)
            if row["avg_apply_sec"] is not None
            else None,
            "p50_apply_sec": float(p50) if p50 is not None else None,
            "p95_apply_sec": float(p95) if p95 is not None else None,
        }

    async def per_account_stats(self, *, since: datetime) -> list[dict[str, Any]]:
        assert self.pool
        sql = (Path(__file__).parent / "sql" / "per_account_stats.sql").read_text(
            encoding="utf-8"
        )
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, since)
        return [dict(r) for r in rows]

    async def pipeline_totals(self, *, since: datetime) -> dict[str, Any]:
        assert self.pool
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM source_messages
                   WHERE ingested_at >= $1) AS messages_ingested,
                  (SELECT count(*) FROM message_ai_screening_runs
                   WHERE created_at >= $1 AND is_latest) AS l2_runs,
                  (SELECT count(*) FROM message_ai_screening_runs
                   WHERE created_at >= $1 AND is_latest AND is_match) AS l2_leads,
                  (SELECT count(*) FROM message_ai_screening_runs
                   WHERE created_at >= $1 AND is_latest AND NOT is_match) AS l2_filtered,
                  (SELECT avg(extract(epoch FROM (delivered_at - created_at)))
                   FROM message_ai_screening_runs
                   WHERE created_at >= $1 AND is_latest
                     AND delivered_at IS NOT NULL) AS avg_delivery_lag_sec
                """,
                since,
            )
        return {
            "messages_ingested": int(row["messages_ingested"] or 0),
            "l2_runs": int(row["l2_runs"] or 0),
            "l2_leads": int(row["l2_leads"] or 0),
            "l2_filtered": int(row["l2_filtered"] or 0),
            "avg_delivery_lag_sec": float(row["avg_delivery_lag_sec"] or 0)
            if row["avg_delivery_lag_sec"] is not None
            else None,
        }
