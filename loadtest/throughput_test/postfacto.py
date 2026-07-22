#!/usr/bin/env python3
"""Post-facto отчёт пропускной способности по следам в PostgreSQL.

Когда harness-отчёт пустой/обороан (нет секции Add) — собираем реальные
цифры из task_queue: сколько parser_add_channel / parser_remove_channel
успело стать done, за какой период, с какой скоростью.

Запуск на vps-104:
  cd ~/Lidogen_telegram_balancer
  python3 -m loadtest.throughput_test.postfacto --hours 12
  python3 -m loadtest.throughput_test.postfacto --from-run 20260721T221400Z
  python3 -m loadtest.throughput_test.postfacto --since 2026-07-21T22:00:00Z --hours 8 \\
      --parser-id 22ee5b37914646bd92a37111661e844e
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import DEFAULT_BASE_URL, _load_dotenv_files, discover_parser_id
from .report import (
    build_phase_metrics,
    compute_latency_from_seconds,
    compute_throughput,
    write_reports,
)

OUT = Path(__file__).resolve().parent / "out"


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _row(r: asyncpg.Record | None) -> dict[str, Any]:
    if r is None:
        return {}
    return {k: _iso(v) for k, v in dict(r).items()}


def _load_run_context(run_id: str) -> dict[str, Any]:
    run_dir = OUT / run_id
    ctx: dict[str, Any] = {"run_id": run_id, "run_dir": str(run_dir)}
    for name in (
        "config.json",
        "state.json",
        "added_channels.json",
        "remove_tasks.json",
        "report.json",
    ):
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            ctx[name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            ctx[name] = {"_error": f"cannot parse {name}"}
    return ctx


def _window_from_run(ctx: dict[str, Any]) -> tuple[datetime, datetime, str | None]:
    """Окно: queue_swap → restore (или now). parser_id из state/config."""
    state = ctx.get("state.json") or {}
    cfg = ctx.get("config.json") or {}
    ts = state.get("phase_timestamps") or {}
    parser_id = state.get("parser_id") or cfg.get("parser_id")

    start = _parse_dt(ts.get("queue_swap") or ts.get("sync_off") or ts.get("preflight"))
    end = _parse_dt(ts.get("restore") or ts.get("report"))
    if start is None:
        # fallback: run_id формата YYYYMMDDTHHMMSSZ
        run_id = str(ctx.get("run_id") or "")
        try:
            start = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            start = datetime.now(timezone.utc) - timedelta(hours=12)
    if end is None:
        end = datetime.now(timezone.utc)
    if end < start:
        end = datetime.now(timezone.utc)
    return start, end, str(parser_id) if parser_id else None


async def collect_task_type(
    conn: asyncpg.Connection,
    *,
    task_type: str,
    since: datetime,
    until: datetime,
    parser_id: str | None,
) -> dict[str, Any]:
    parser_clause = ""
    args: list[Any] = [task_type, since, until]
    if parser_id:
        parser_clause = "AND payload->>'parser_id' = $4"
        args.append(parser_id)

    stats = await conn.fetchrow(
        f"""
        SELECT
          count(*)::int AS total,
          count(*) FILTER (WHERE status = 'done')::int AS done,
          count(*) FILTER (WHERE status = 'failed')::int AS failed,
          count(*) FILTER (WHERE status = 'cancelled')::int AS cancelled,
          count(*) FILTER (WHERE status = 'stuck')::int AS stuck,
          count(*) FILTER (
            WHERE status IN ('queued','scheduled','retry','in_progress')
          )::int AS pending,
          count(*) FILTER (
            WHERE last_error ILIKE 'insufficient_resource%'
          )::int AS insuff_resource,
          min(created_at) AS first_created,
          max(created_at) AS last_created,
          min(finished_at) FILTER (WHERE status = 'done') AS first_done_at,
          max(finished_at) FILTER (WHERE status = 'done') AS last_done_at,
          avg(EXTRACT(EPOCH FROM (finished_at - created_at)))
            FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
            AS avg_latency_sec
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2
          AND created_at < $3
          {parser_clause}
        """,
        *args,
    )

    latency_samples = await conn.fetch(
        f"""
        SELECT EXTRACT(EPOCH FROM (finished_at - created_at))::float AS sec
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2 AND created_at < $3
          AND status = 'done'
          AND finished_at IS NOT NULL
          {parser_clause}
        ORDER BY finished_at
        """,
        *args,
    )

    by_status = await conn.fetch(
        f"""
        SELECT status::text AS status, count(*)::int AS cnt
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2 AND created_at < $3
          {parser_clause}
        GROUP BY status
        ORDER BY cnt DESC
        """,
        *args,
    )

    hourly = await conn.fetch(
        f"""
        SELECT
          date_trunc('hour', finished_at) AS hour,
          count(*)::int AS done
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2 AND created_at < $3
          AND status = 'done'
          AND finished_at IS NOT NULL
          {parser_clause}
        GROUP BY 1
        ORDER BY 1
        """,
        *args,
    )

    per_account = await conn.fetch(
        f"""
        SELECT
          t.account_id,
          a.session_name,
          count(*) FILTER (WHERE t.status = 'done')::int AS done,
          count(*) FILTER (WHERE t.status = 'failed')::int AS failed,
          count(*) FILTER (
            WHERE t.status IN ('queued','scheduled','retry','in_progress')
          )::int AS pending,
          count(*)::int AS total,
          avg(EXTRACT(EPOCH FROM (t.finished_at - t.created_at)))
            FILTER (WHERE t.status = 'done' AND t.finished_at IS NOT NULL)
            AS avg_latency_sec
        FROM task_queue t
        LEFT JOIN accounts a ON a.id = t.account_id
        WHERE t.task_type_code = $1
          AND t.created_at >= $2 AND t.created_at < $3
          {parser_clause}
        GROUP BY t.account_id, a.session_name
        ORDER BY done DESC NULLS LAST, total DESC
        LIMIT 40
        """,
        *args,
    )

    errors = await conn.fetch(
        f"""
        SELECT
          COALESCE(NULLIF(trim(last_error), ''), '(empty)') AS error,
          count(*)::int AS count
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2 AND created_at < $3
          AND status IN ('failed', 'retry', 'cancelled')
          AND last_error IS NOT NULL
          AND last_error <> ''
          {parser_clause}
        GROUP BY 1
        ORDER BY count DESC
        LIMIT 25
        """,
        *args,
    )

    # done, завершённые в окне (даже если created раньше) — доп. срез
    done_in_window = await conn.fetchrow(
        f"""
        SELECT count(*)::int AS done_finished_in_window
        FROM task_queue
        WHERE task_type_code = $1
          AND status = 'done'
          AND finished_at IS NOT NULL
          AND finished_at >= $2 AND finished_at < $3
          {parser_clause}
        """,
        *args,
    )

    status_counts = {str(r["status"]): int(r["cnt"]) for r in by_status}
    total = int(stats["total"] or 0) if stats else 0
    done = int(stats["done"] or 0) if stats else 0

    first_created = stats["first_created"] if stats else None
    last_done = stats["last_done_at"] if stats else None
    first_done = stats["first_done_at"] if stats else None

    wall_sec = max(1.0, (until - since).total_seconds())
    # Primary для capacity: wall-окно наблюдения (since→until).
    # Span first_created→last_done занижает/искажает скорость при длинной
    # очереди (латентность очереди попадает в знаменатель).
    if first_done and last_done and last_done > first_done:
        span_active = (last_done - first_done).total_seconds()
        span_basis = "first_done→last_done"
    elif first_created and last_done and last_done > first_created:
        span_active = (last_done - first_created).total_seconds()
        span_basis = "first_created→last_done"
    else:
        span_active = wall_sec
        span_basis = "wall_since→until"

    thr_wall = compute_throughput(
        done_count=done, window_sec=wall_sec, elapsed_sec=wall_sec
    )
    thr_span = compute_throughput(
        done_count=done, window_sec=wall_sec, elapsed_sec=span_active
    )

    secs = [float(r["sec"]) for r in latency_samples if r["sec"] is not None]
    latency = compute_latency_from_seconds(secs)
    latency["basis"] = "created→finished (queue+exec)"
    if latency.get("count") and stats and stats["avg_latency_sec"] is not None:
        latency["avg_sec"] = round(float(stats["avg_latency_sec"]), 3)

    exec_row = await conn.fetchrow(
        f"""
        SELECT
          count(*)::int AS exec_count,
          avg(
            EXTRACT(EPOCH FROM (finished_at - COALESCE(started_at, locked_at)))
          ) AS exec_avg_sec,
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (
              finished_at - COALESCE(started_at, locked_at)
            ))
          ) AS exec_p50_sec
        FROM task_queue
        WHERE task_type_code = $1
          AND created_at >= $2 AND created_at < $3
          AND status = 'done'
          AND finished_at IS NOT NULL
          AND COALESCE(started_at, locked_at) IS NOT NULL
          {parser_clause}
        """,
        *args,
    )
    if exec_row and int(exec_row["exec_count"] or 0) > 0:
        latency["exec_count"] = int(exec_row["exec_count"])
        latency["exec_avg_sec"] = (
            round(float(exec_row["exec_avg_sec"]), 3)
            if exec_row["exec_avg_sec"] is not None
            else None
        )
        latency["exec_p50_sec"] = (
            round(float(exec_row["exec_p50_sec"]), 3)
            if exec_row["exec_p50_sec"] is not None
            else None
        )

    metrics = build_phase_metrics(
        label=f"postfacto:{task_type}",
        enqueued=total,
        status_counts=status_counts,
        window_sec=wall_sec,
        elapsed_sec=wall_sec,
        latency=latency,
        hourly=[
            {"hour": _iso(r["hour"]), "done": int(r["done"])} for r in hourly
        ],
        per_account=[_row(r) for r in per_account],
        errors=[_row(r) for r in errors],
    )
    metrics["first_created"] = _iso(first_created)
    metrics["last_created"] = _iso(stats["last_created"]) if stats else None
    metrics["first_done_at"] = _iso(first_done)
    metrics["last_done_at"] = _iso(last_done)
    metrics["elapsed_basis"] = "wall_since→until"
    metrics["span_basis"] = span_basis
    metrics["throughput_over_span"] = thr_span
    metrics["throughput_over_wall"] = thr_wall
    metrics["done_finished_in_window"] = int(
        (done_in_window or {}).get("done_finished_in_window") or 0
    )
    metrics["insuff_resource"] = int(stats["insuff_resource"] or 0) if stats else 0
    metrics["scope"] = "created_at_window"
    return metrics


async def collect_by_task_ids(
    conn: asyncpg.Connection,
    *,
    task_ids: list[int],
    label: str,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Метрики строго по списку task_id прогона (источник истины harness)."""
    if not task_ids:
        empty = build_phase_metrics(
            label=label,
            enqueued=0,
            status_counts={},
            window_sec=max(1.0, (until - since).total_seconds()),
            elapsed_sec=max(1.0, (until - since).total_seconds()),
            latency={"count": 0},
            hourly=[],
            per_account=[],
            errors=[],
        )
        empty["scope"] = "task_ids"
        empty["elapsed_basis"] = "wall_since→until"
        empty["throughput_over_wall"] = empty["throughput"]
        empty["throughput_over_span"] = empty["throughput"]
        return empty

    ids = list(dict.fromkeys(int(x) for x in task_ids))
    wall_sec = max(1.0, (until - since).total_seconds())

    by_status = await conn.fetch(
        """
        SELECT status::text AS status, count(*)::int AS cnt
        FROM task_queue
        WHERE id = ANY($1::bigint[])
        GROUP BY status
        ORDER BY cnt DESC
        """,
        ids,
    )
    status_counts = {str(r["status"]): int(r["cnt"]) for r in by_status}
    done = int(status_counts.get("done", 0))

    stats = await conn.fetchrow(
        """
        SELECT
          min(created_at) AS first_created,
          max(created_at) AS last_created,
          min(finished_at) FILTER (WHERE status = 'done') AS first_done_at,
          max(finished_at) FILTER (WHERE status = 'done') AS last_done_at,
          avg(EXTRACT(EPOCH FROM (finished_at - created_at)))
            FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
            AS avg_latency_sec
        FROM task_queue
        WHERE id = ANY($1::bigint[])
        """,
        ids,
    )

    latency_samples = await conn.fetch(
        """
        SELECT EXTRACT(EPOCH FROM (finished_at - created_at))::float AS sec
        FROM task_queue
        WHERE id = ANY($1::bigint[])
          AND status = 'done'
          AND finished_at IS NOT NULL
        """,
        ids,
    )
    secs = [float(r["sec"]) for r in latency_samples if r["sec"] is not None]
    latency = compute_latency_from_seconds(secs)
    latency["basis"] = "created→finished (queue+exec)"
    if latency.get("count") and stats and stats["avg_latency_sec"] is not None:
        latency["avg_sec"] = round(float(stats["avg_latency_sec"]), 3)

    exec_row = await conn.fetchrow(
        """
        SELECT
          count(*)::int AS exec_count,
          avg(
            EXTRACT(EPOCH FROM (finished_at - COALESCE(started_at, locked_at)))
          ) AS exec_avg_sec,
          percentile_cont(0.5) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (
              finished_at - COALESCE(started_at, locked_at)
            ))
          ) AS exec_p50_sec
        FROM task_queue
        WHERE id = ANY($1::bigint[])
          AND status = 'done'
          AND finished_at IS NOT NULL
          AND COALESCE(started_at, locked_at) IS NOT NULL
        """,
        ids,
    )
    if exec_row and int(exec_row["exec_count"] or 0) > 0:
        latency["exec_count"] = int(exec_row["exec_count"])
        latency["exec_avg_sec"] = (
            round(float(exec_row["exec_avg_sec"]), 3)
            if exec_row["exec_avg_sec"] is not None
            else None
        )
        latency["exec_p50_sec"] = (
            round(float(exec_row["exec_p50_sec"]), 3)
            if exec_row["exec_p50_sec"] is not None
            else None
        )

    hourly = await conn.fetch(
        """
        SELECT date_trunc('hour', finished_at) AS hour, count(*)::int AS done
        FROM task_queue
        WHERE id = ANY($1::bigint[])
          AND status = 'done'
          AND finished_at IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
        ids,
    )
    per_account = await conn.fetch(
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
          avg(EXTRACT(EPOCH FROM (t.finished_at - t.created_at)))
            FILTER (WHERE t.status = 'done' AND t.finished_at IS NOT NULL)
            AS avg_latency_sec
        FROM task_queue t
        LEFT JOIN accounts a ON a.id = t.account_id
        WHERE t.id = ANY($1::bigint[])
        GROUP BY t.account_id, a.session_name
        ORDER BY done DESC NULLS LAST, total DESC
        LIMIT 40
        """,
        ids,
    )
    errors = await conn.fetch(
        """
        SELECT
          COALESCE(NULLIF(trim(last_error), ''), '(empty)') AS error,
          count(*)::int AS count
        FROM task_queue
        WHERE id = ANY($1::bigint[])
          AND status IN ('failed', 'retry', 'cancelled')
          AND last_error IS NOT NULL
          AND last_error <> ''
        GROUP BY 1
        ORDER BY count DESC
        LIMIT 25
        """,
        ids,
    )

    first_created = stats["first_created"] if stats else None
    first_done = stats["first_done_at"] if stats else None
    last_done = stats["last_done_at"] if stats else None
    if first_done and last_done and last_done > first_done:
        span_active = (last_done - first_done).total_seconds()
        span_basis = "first_done→last_done"
    elif first_created and last_done and last_done > first_created:
        span_active = (last_done - first_created).total_seconds()
        span_basis = "first_created→last_done"
    else:
        span_active = wall_sec
        span_basis = "wall_since→until"

    thr_wall = compute_throughput(
        done_count=done, window_sec=wall_sec, elapsed_sec=wall_sec
    )
    thr_span = compute_throughput(
        done_count=done, window_sec=wall_sec, elapsed_sec=span_active
    )

    metrics = build_phase_metrics(
        label=label,
        enqueued=len(ids),
        status_counts=status_counts,
        window_sec=wall_sec,
        elapsed_sec=wall_sec,
        latency=latency,
        hourly=[
            {"hour": _iso(r["hour"]), "done": int(r["done"])} for r in hourly
        ],
        per_account=[_row(r) for r in per_account],
        errors=[_row(r) for r in errors],
    )
    metrics["first_created"] = _iso(first_created)
    metrics["last_created"] = _iso(stats["last_created"]) if stats else None
    metrics["first_done_at"] = _iso(first_done)
    metrics["last_done_at"] = _iso(last_done)
    metrics["elapsed_basis"] = "wall_since→until"
    metrics["span_basis"] = span_basis
    metrics["throughput_over_span"] = thr_span
    metrics["throughput_over_wall"] = thr_wall
    metrics["scope"] = "task_ids"
    metrics["task_ids_count"] = len(ids)
    return metrics


async def collect(
    *,
    since: datetime,
    until: datetime,
    parser_id: str | None,
    base_url: str,
    add_task_ids: list[int] | None = None,
    remove_task_ids: list[int] | None = None,
) -> dict[str, Any]:
    dsn = (
        os.environ.get("QUEUE_DATABASE_URL")
        or os.environ.get("THROUGHPUT_PGURL")
        or os.environ.get("PGURL")
        or ""
    )
    if not dsn:
        raise SystemExit("Нужен QUEUE_DATABASE_URL (или в standalone_discovery/.env)")

    conn = await asyncpg.connect(dsn)
    try:
        if add_task_ids:
            add = await collect_by_task_ids(
                conn,
                task_ids=add_task_ids,
                label="postfacto:parser_add_channel",
                since=since,
                until=until,
            )
        else:
            add = await collect_task_type(
                conn,
                task_type="parser_add_channel",
                since=since,
                until=until,
                parser_id=parser_id,
            )
        if remove_task_ids is not None:
            rem = await collect_by_task_ids(
                conn,
                task_ids=remove_task_ids,
                label="postfacto:parser_remove_channel",
                since=since,
                until=until,
            )
        else:
            rem = await collect_task_type(
                conn,
                task_type="parser_remove_channel",
                since=since,
                until=until,
                parser_id=parser_id,
            )
        accounts = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (
                WHERE is_enabled AND status IN ('active','cooldown')
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
              )::int AS pickable,
              count(*) FILTER (WHERE current_task_id IS NOT NULL)::int AS busy,
              count(*) FILTER (
                WHERE cooldown_until IS NOT NULL AND cooldown_until > now()
              )::int AS on_cooldown
            FROM accounts
            """
        )
        paused = await conn.fetchrow(
            """
            SELECT count(*)::int AS cnt
            FROM task_queue
            WHERE status = 'cancelled'
              AND last_error LIKE 'throughput-test-paused:%'
            """
        )
    finally:
        await conn.close()

    metrics_now = None
    api_key = os.environ.get("API_KEY") or os.environ.get("THROUGHPUT_API_KEY") or ""
    if api_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(
                    f"{base_url.rstrip('/')}/discovery-api/parser/queue/metrics",
                    headers={"X-API-Key": api_key},
                )
                if r.status_code < 400:
                    metrics_now = r.json()
        except Exception as exc:  # noqa: BLE001
            metrics_now = {"_error": str(exc)}

    scope_note = (
        "Post-facto по task_ids прогона (точный scope)."
        if add_task_ids
        else (
            "Post-facto по created_at-окну + parser_id. "
            "Может включать чужие add того же parser (нет created_by=throughput_test). "
            "Лучше: --from-run с сохранёнными task_ids."
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "wall_hours": round((until - since).total_seconds() / 3600.0, 3),
        "parser_id": parser_id,
        "base_url": base_url,
        "note": (
            f"{scope_note} Primary скорость = done/wall. "
            "Латентность created→finished = очередь+exec; отдельно exec_*."
        ),
        "add": add,
        "remove": rem,
        "accounts_now": _row(accounts),
        "still_paused_by_throughput_test": int((paused or {}).get("cnt") or 0),
        "queue_metrics_now": metrics_now,
    }


def _task_ids_from_run(ctx: dict[str, Any]) -> tuple[list[int] | None, list[int] | None]:
    """Достать add/remove task_ids из state / added_channels / remove_tasks."""
    state = ctx.get("state.json") or {}
    added = ctx.get("added_channels.json") or {}
    remove_file = ctx.get("remove_tasks.json") or {}

    add_ids = state.get("add_task_ids") or added.get("task_ids") or None
    rem_ids = state.get("remove_task_ids")
    if rem_ids is None and "task_ids" in remove_file:
        rem_ids = remove_file.get("task_ids") or []

    def _norm(raw: Any) -> list[int] | None:
        if raw is None:
            return None
        out: list[int] = []
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out

    return _norm(add_ids), _norm(rem_ids)


def _print_summary(data: dict[str, Any]) -> None:
    add = data.get("add") or {}
    rem = data.get("remove") or {}
    thr = add.get("throughput_over_wall") or add.get("throughput") or {}
    print("")
    print("=== POSTFACTO SUMMARY ===")
    print(f"window: {data.get('since')} → {data.get('until')} ({data.get('wall_hours')}h)")
    print(f"parser_id: {data.get('parser_id')}")
    print(
        f"ADD:    scope={add.get('scope')} enqueued={add.get('enqueued')} "
        f"done={add.get('done')} failed={add.get('failed')} "
        f"pending={add.get('pending')} "
        f"| {thr.get('per_hour')} задач/час "
        f"(basis={add.get('elapsed_basis')}, elapsed={thr.get('elapsed_sec')}s)"
    )
    thr_r = rem.get("throughput_over_wall") or rem.get("throughput") or {}
    print(
        f"REMOVE: scope={rem.get('scope')} enqueued={rem.get('enqueued')} "
        f"done={rem.get('done')} failed={rem.get('failed')} "
        f"pending={rem.get('pending')} "
        f"| {thr_r.get('per_hour')} задач/час"
    )
    print(f"still paused (throughput-test-paused:*): {data.get('still_paused_by_throughput_test')}")
    print("")


async def amain(argv: list[str] | None = None) -> int:
    _load_dotenv_files()

    p = argparse.ArgumentParser(
        description="Post-facto отчёт throughput из PG (реальные цифры add/remove)"
    )
    p.add_argument(
        "--from-run",
        metavar="RUN_ID",
        help="Взять окно и parser_id из loadtest/throughput_test/out/<RUN_ID>/",
    )
    p.add_argument("--hours", type=float, default=12.0, help="Окно назад от --until (default 12)")
    p.add_argument("--since", default=None, help="ISO начало окна (перекрывает --hours)")
    p.add_argument("--until", default=None, help="ISO конец окна (default now)")
    p.add_argument(
        "--parser-id",
        default=os.environ.get("THROUGHPUT_PARSER_ID"),
        help="Фильтр по payload.parser_id (default: автоподхват / из --from-run)",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("THROUGHPUT_BASE_URL", DEFAULT_BASE_URL),
    )
    p.add_argument(
        "--all-parsers",
        action="store_true",
        help="Не фильтровать по parser_id (все add/remove в окне)",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Куда писать report (default: out/postfacto-<ts>/ или out/<run>/postfacto/)",
    )
    args = p.parse_args(argv)

    run_ctx: dict[str, Any] | None = None
    parser_id = args.parser_id
    since = _parse_dt(args.since)
    until = _parse_dt(args.until) or datetime.now(timezone.utc)

    if args.from_run:
        run_ctx = _load_run_context(args.from_run)
        run_since, run_until, run_parser = _window_from_run(run_ctx)
        if since is None:
            since = run_since
        if args.until is None:
            until = run_until
        if not parser_id:
            parser_id = run_parser

    if since is None:
        since = until - timedelta(hours=float(args.hours))

    if args.all_parsers:
        parser_id = None
    elif not parser_id:
        api_key = os.environ.get("API_KEY") or ""
        if api_key:
            try:
                parser_id = discover_parser_id(
                    base_url=args.base_url, api_key=api_key
                )
                print(f"Автоподхват parser_id={parser_id}")
            except SystemExit as exc:
                print(f"parser_id не задан и автоподхват не удался: {exc}")
                print("Считаем по всем parser_id (--all-parsers)")
                parser_id = None
        else:
            print("parser_id не задан, API_KEY нет — считаем по всем (--all-parsers)")
            parser_id = None

    add_task_ids: list[int] | None = None
    remove_task_ids: list[int] | None = None
    run_cfg: dict[str, Any] = {}
    if run_ctx:
        add_task_ids, remove_task_ids = _task_ids_from_run(run_ctx)
        run_cfg = run_ctx.get("config.json") or {}
        if add_task_ids:
            print(f"Scope ADD: {len(add_task_ids)} task_ids из --from-run")
        else:
            print(
                "WARN: в --from-run нет add_task_ids — fallback на created_at-окно "
                "(может смешать чужие задачи того же parser_id)"
            )
        if remove_task_ids is not None:
            print(f"Scope REMOVE: {len(remove_task_ids)} task_ids из --from-run")

    data = await collect(
        since=since,
        until=until,
        parser_id=parser_id,
        base_url=str(args.base_url).rstrip("/"),
        add_task_ids=add_task_ids,
        remove_task_ids=remove_task_ids,
    )
    if run_ctx:
        data["from_run"] = {
            "run_id": run_ctx.get("run_id"),
            "harness_phase_timestamps": (run_ctx.get("state.json") or {}).get(
                "phase_timestamps"
            ),
            "harness_had_add": bool(
                (run_ctx.get("added_channels.json") or {}).get("channels")
                or (run_ctx.get("state.json") or {}).get("add_task_ids")
            ),
            "add_task_ids_used": len(add_task_ids or []),
            "remove_task_ids_used": (
                len(remove_task_ids) if remove_task_ids is not None else None
            ),
        }

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif args.from_run:
        out_dir = OUT / args.from_run / "postfacto"
    else:
        out_dir = OUT / (
            "postfacto-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path, json_path = write_reports(
        run_dir=out_dir,
        run_id=str(args.from_run or out_dir.name),
        parser_id=str(parser_id or "(all)"),
        config_snapshot={
            "mode": "postfacto",
            "since": since.isoformat(),
            "until": until.isoformat(),
            "wall_hours": data.get("wall_hours"),
            "from_run": args.from_run,
            "scope": (data.get("add") or {}).get("scope"),
            "all_parsers": bool(args.all_parsers) or parser_id is None,
            "add_count": run_cfg.get("add_count"),
            "wait_recovery_sec": run_cfg.get("wait_recovery_sec"),
            "add_window_sec": run_cfg.get("add_window_sec"),
            "remove_window_sec": run_cfg.get("remove_window_sec"),
        },
        add_metrics=data.get("add"),
        remove_metrics=data.get("remove"),
        restore_result={
            "still_paused_by_throughput_test": data.get(
                "still_paused_by_throughput_test"
            ),
        },
        sync_restore=None,
        foreign_tasks=None,
        phase_timestamps={
            "postfacto_since": since.isoformat(),
            "postfacto_until": until.isoformat(),
        },
        notes=[
            data.get("note") or "",
            f"accounts_now={data.get('accounts_now')}",
            (
                f"harness_had_add={data.get('from_run', {}).get('harness_had_add')}"
                if data.get("from_run")
                else ""
            ),
        ],
    )
    # дополним сырой dump
    (out_dir / "postfacto_raw.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    _print_summary(data)
    print(f"report.md:  {md_path}")
    print(f"report.json: {json_path}")
    print(f"raw:         {out_dir / 'postfacto_raw.json'}")
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(amain(argv)))


if __name__ == "__main__":
    main()
