#!/usr/bin/env python3
"""Post-facto отчёт по следам loadtest в PG (когда out/ пуст).

Запуск на vps-104:
  cd ~/Lidogen_telegram_balancer
  export QUEUE_DATABASE_URL="$(grep ^QUEUE_DATABASE_URL= standalone_discovery/.env | cut -d= -f2- | tr -d '\\r')"
  export API_KEY="$(grep ^API_KEY= standalone_discovery/.env | cut -d= -f2- | tr -d '\\r')"
  python3 -m loadtest.prod_e2e.postfacto --hours 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx

OUT = Path(__file__).resolve().parent / "out"


def _env_url() -> str:
    return os.environ.get("QUEUE_DATABASE_URL") or os.environ.get("PGURL") or ""


def _api_key() -> str:
    return os.environ.get("API_KEY") or os.environ.get("LOADTEST_API_KEY") or ""


async def collect(hours: float, base_url: str) -> dict:
    dsn = _env_url()
    if not dsn:
        raise SystemExit("Нужен QUEUE_DATABASE_URL или PGURL")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    conn = await asyncpg.connect(dsn)
    try:
        projects = await conn.fetch(
            """
            SELECT id, name, status, created_at, updated_at, archived_at
            FROM monitoring_projects
            WHERE name LIKE 'LOADTEST-%'
              AND (created_at >= $1 OR updated_at >= $1)
            ORDER BY created_at DESC
            """,
            since,
        )
        links = await conn.fetch(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE psc.is_enabled) AS enabled,
              count(*) FILTER (WHERE NOT psc.is_enabled) AS disabled
            FROM project_source_channels psc
            JOIN monitoring_projects mp ON mp.id = psc.monitoring_project_id
            WHERE mp.name LIKE 'LOADTEST-%'
              AND (mp.created_at >= $1 OR mp.updated_at >= $1 OR psc.updated_at >= $1)
            """,
            since,
        )
        add_stats = await conn.fetchrow(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE status = 'done') AS done,
              count(*) FILTER (WHERE status = 'failed') AS failed,
              count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
              count(*) FILTER (WHERE status = 'stuck') AS stuck,
              count(*) FILTER (
                WHERE status IN ('queued','scheduled','retry','in_progress')
              ) AS active,
              count(*) FILTER (
                WHERE last_error ILIKE 'insufficient_resource%'
              ) AS insuff,
              min(created_at) AS first_created,
              max(finished_at) AS last_finished,
              avg(extract(epoch FROM (finished_at - created_at)))
                FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
                AS avg_apply_sec
            FROM task_queue
            WHERE task_type_code = 'parser_add_channel'
              AND created_at >= $1
              AND (
                    created_by = 'discovery_api:add-channels'
                 OR payload->>'parser_id' IS NOT NULL
              )
            """,
            since,
        )
        add_by_status = await conn.fetch(
            """
            SELECT status::text, count(*) AS cnt
            FROM task_queue
            WHERE task_type_code = 'parser_add_channel'
              AND created_at >= $1
            GROUP BY status
            ORDER BY cnt DESC
            """,
            since,
        )
        remove_stats = await conn.fetchrow(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE status = 'done') AS done,
              avg(extract(epoch FROM (finished_at - created_at)))
                FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
                AS avg_apply_sec
            FROM task_queue
            WHERE task_type_code = 'parser_remove_channel'
              AND created_at >= $1
            """,
            since,
        )
        accounts = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (
                WHERE is_enabled AND status IN ('active','cooldown')
                  AND current_task_id IS NULL
                  AND (cooldown_until IS NULL OR cooldown_until <= now())
              ) AS pickable,
              count(*) FILTER (WHERE current_task_id IS NOT NULL) AS busy,
              count(*) FILTER (WHERE status = 'banned') AS banned
            FROM accounts
            """
        )
        per_account = await conn.fetch(
            """
            SELECT
              a.id AS account_id,
              a.session_name,
              COUNT(DISTINCT sm.id) AS messages_total,
              COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest) AS l2_total,
              COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest AND masr.is_match) AS l2_leads,
              COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest AND NOT masr.is_match) AS l2_filtered,
              MIN(sm.ingested_at) AS first_message_at,
              MIN(masr.created_at) FILTER (WHERE masr.is_latest AND masr.is_match) AS first_lead_at,
              EXTRACT(EPOCH FROM (
                MIN(masr.created_at) FILTER (WHERE masr.is_latest AND masr.is_match)
                - MIN(sm.ingested_at)
              )) AS time_to_first_lead_sec
            FROM accounts a
            LEFT JOIN source_channels sc ON sc.assigned_account_id = a.id
            LEFT JOIN source_messages sm
              ON sm.source_channel_id = sc.id AND sm.ingested_at >= $1
            LEFT JOIN message_ai_screening_runs masr
              ON masr.source_message_id = sm.id AND masr.created_at >= $1
            WHERE a.is_enabled = true
            GROUP BY a.id, a.session_name
            HAVING COUNT(DISTINCT sm.id) > 0
                OR COUNT(DISTINCT masr.id) FILTER (WHERE masr.is_latest) > 0
            ORDER BY messages_total DESC NULLS LAST
            LIMIT 50
            """,
            since,
        )
        pipeline = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM source_messages WHERE ingested_at >= $1) AS messages_ingested,
              (SELECT count(*) FROM message_ai_screening_runs
               WHERE created_at >= $1 AND is_latest) AS l2_runs,
              (SELECT count(*) FROM message_ai_screening_runs
               WHERE created_at >= $1 AND is_latest AND is_match) AS l2_leads,
              (SELECT count(*) FROM message_ai_screening_runs
               WHERE created_at >= $1 AND is_latest AND NOT is_match) AS l2_filtered
            """,
            since,
        )
        recent_errors = await conn.fetch(
            """
            SELECT id, status, left(coalesce(last_error,''), 120) AS err, created_at, finished_at
            FROM task_queue
            WHERE task_type_code IN ('parser_add_channel','parser_remove_channel')
              AND created_at >= $1
              AND last_error IS NOT NULL
              AND last_error <> ''
            ORDER BY updated_at DESC
            LIMIT 30
            """,
            since,
        )
    finally:
        await conn.close()

    metrics = None
    key = _api_key()
    if key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(
                    f"{base_url.rstrip('/')}/discovery-api/parser/queue/metrics",
                    headers={"X-API-Key": key},
                )
                if r.status_code < 400:
                    metrics = r.json()
        except Exception as exc:  # noqa: BLE001
            metrics = {"_error": str(exc)}

    def _row(r):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since.isoformat(),
        "hours": hours,
        "why_out_empty_hint": (
            "Каталог out/ пуст = python -m loadtest.prod_e2e не создал run_dir "
            "(не стартовал / упал на argparse / другой cwd). Ниже — следы в PG."
        ),
        "loadtest_projects": [_row(r) for r in projects],
        "loadtest_links": _row(links[0]) if links else {},
        "parser_add_channel": _row(add_stats) if add_stats else {},
        "parser_add_by_status": [_row(r) for r in add_by_status],
        "parser_remove_channel": _row(remove_stats) if remove_stats else {},
        "accounts_now": _row(accounts) if accounts else {},
        "per_account": [_row(r) for r in per_account],
        "pipeline": _row(pipeline) if pipeline else {},
        "recent_task_errors": [_row(r) for r in recent_errors],
        "queue_metrics_now": metrics,
    }


def to_md(data: dict) -> str:
    lines = [
        f"# Post-facto loadtest report",
        "",
        f"- generated_at: `{data['generated_at']}`",
        f"- window: last **{data['hours']}h** (since `{data['since']}`)",
        f"- note: {data['why_out_empty_hint']}",
        "",
        "## LOADTEST projects",
        "",
    ]
    projs = data.get("loadtest_projects") or []
    if not projs:
        lines.append("_Нет проектов `LOADTEST-%` за окно — harness, скорее всего, не дошёл до seed._")
    else:
        lines.append("| id | name | status | created_at |")
        lines.append("|---:|---|---|---|")
        for p in projs:
            lines.append(
                f"| {p.get('id')} | {p.get('name')} | {p.get('status')} | {p.get('created_at')} |"
            )
    lines += ["", f"Links: `{data.get('loadtest_links')}`", "", "## parser_add_channel", ""]
    add = data.get("parser_add_channel") or {}
    for k, v in add.items():
        lines.append(f"- `{k}`: **{v}**")
    lines += ["", "By status:", ""]
    for r in data.get("parser_add_by_status") or []:
        lines.append(f"- `{r.get('status')}`: {r.get('cnt')}")
    lines += ["", "## parser_remove_channel", f"`{data.get('parser_remove_channel')}`", ""]
    lines += ["## Accounts now", f"`{data.get('accounts_now')}`", ""]
    lines += ["## Pipeline", f"`{data.get('pipeline')}`", ""]
    lines += ["## Per-account (messages / L2 / leads)", ""]
    rows = data.get("per_account") or []
    if not rows:
        lines.append("_Нет сообщений/L2 за окно._")
    else:
        lines.append("| account | session | msg | L2 | leads | filtered | ttf_s |")
        lines.append("|---:|---|---:|---:|---:|---:|---:|")
        for r in rows:
            lines.append(
                f"| {r.get('account_id')} | {r.get('session_name')} | {r.get('messages_total')} | "
                f"{r.get('l2_total')} | {r.get('l2_leads')} | {r.get('l2_filtered')} | "
                f"{r.get('time_to_first_lead_sec')} |"
            )
    lines += ["", "## Recent task errors (sample)", ""]
    for e in data.get("recent_task_errors") or []:
        lines.append(f"- id={e.get('id')} {e.get('status')}: `{e.get('err')}`")
    lines += ["", "## queue/metrics now", "", "```json", json.dumps(data.get("queue_metrics_now"), ensure_ascii=False, indent=2, default=str)[:8000], "```", ""]
    return "\n".join(lines) + "\n"


async def amain() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=6.0)
    p.add_argument(
        "--base-url",
        default=os.environ.get(
            "LOADTEST_BASE_URL",
            "https://lidogen-balancer-tg-prod.web.oboyma.ai",
        ),
    )
    args = p.parse_args()
    data = await collect(args.hours, args.base_url)
    run_id = "postfacto-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = to_md(data)
    md_path = out_dir / "report.md"
    md_path.write_text(md, encoding="utf-8")
    print(str(md_path))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
