"""Метрики и итоговый отчёт throughput-теста."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def compute_throughput(
    *,
    done_count: int,
    window_sec: float,
    elapsed_sec: float | None = None,
) -> dict[str, float]:
    """Скорость разбора: задач/час и задач/мин по окну наблюдения."""
    effective = float(elapsed_sec if elapsed_sec is not None else window_sec)
    if effective <= 0:
        return {"per_hour": 0.0, "per_minute": 0.0, "elapsed_sec": 0.0}
    per_hour = done_count * 3600.0 / effective
    per_minute = done_count * 60.0 / effective
    return {
        "per_hour": round(per_hour, 3),
        "per_minute": round(per_minute, 3),
        "elapsed_sec": round(effective, 1),
    }


def compute_latency_from_seconds(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        if n == 1:
            return ordered[0]
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return ordered[idx]

    return {
        "count": n,
        "avg_sec": round(statistics.fmean(ordered), 3),
        "p50_sec": round(pct(50), 3),
        "p95_sec": round(pct(95), 3),
        "min_sec": round(ordered[0], 3),
        "max_sec": round(ordered[-1], 3),
    }


def append_timeline_row(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k) for k in fieldnames})


def build_phase_metrics(
    *,
    label: str,
    enqueued: int,
    status_counts: dict[str, int],
    window_sec: float,
    elapsed_sec: float,
    latency: dict[str, Any],
    hourly: list[dict[str, Any]],
    per_account: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    done = int(status_counts.get("done", 0))
    failed = int(status_counts.get("failed", 0))
    pending = sum(
        int(status_counts.get(s, 0))
        for s in ("queued", "scheduled", "retry", "in_progress", "stuck")
    )
    cancelled = int(status_counts.get("cancelled", 0))
    speed = compute_throughput(
        done_count=done, window_sec=window_sec, elapsed_sec=elapsed_sec
    )
    return {
        "label": label,
        "enqueued": enqueued,
        "done": done,
        "failed": failed,
        "pending": pending,
        "cancelled": cancelled,
        "status_counts": status_counts,
        "window_sec": window_sec,
        "elapsed_sec": elapsed_sec,
        "throughput": speed,
        "latency": latency,
        "hourly_done": hourly,
        "per_account": per_account,
        "top_errors": errors,
        "completion_ratio": (done / enqueued) if enqueued else 0.0,
    }


def write_reports(
    *,
    run_dir: Path,
    run_id: str,
    parser_id: str,
    config_snapshot: dict[str, Any],
    add_metrics: dict[str, Any] | None,
    remove_metrics: dict[str, Any] | None,
    restore_result: dict[str, Any] | None,
    sync_restore: dict[str, Any] | None,
    foreign_tasks: dict[str, Any] | None,
    phase_timestamps: dict[str, str],
    notes: list[str] | None = None,
) -> tuple[Path, Path]:
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "parser_id": parser_id,
        "config": config_snapshot,
        "phase_timestamps": phase_timestamps,
        "add": add_metrics,
        "remove": remove_metrics,
        "restore": restore_result,
        "sync_restore": sync_restore,
        "foreign_tasks_in_window": foreign_tasks,
        "notes": notes or [],
    }
    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(
        _render_markdown(payload),
        encoding="utf-8",
    )
    return md_path, json_path


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Throughput test report — `{payload['run_id']}`")
    lines.append("")
    lines.append(f"Сгенерирован: `{payload['generated_at']}`")
    lines.append(f"parser_id: `{payload['parser_id']}`")
    lines.append("")

    cfg = payload.get("config") or {}
    lines.append("## Конфиг")
    lines.append("")
    lines.append(f"- add_count: **{cfg.get('add_count')}**")
    lines.append(f"- wait_recovery_sec: **{cfg.get('wait_recovery_sec')}**")
    lines.append(f"- add_window_sec: **{cfg.get('add_window_sec')}**")
    lines.append(f"- remove_window_sec: **{cfg.get('remove_window_sec')}**")
    lines.append("")

    ts = payload.get("phase_timestamps") or {}
    if ts:
        lines.append("## Таймлайн фаз")
        lines.append("")
        for k, v in ts.items():
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    add = payload.get("add")
    if add:
        lines.extend(_section_metrics("Add (`parser_add_channel`)", add))

    rem = payload.get("remove")
    if rem:
        lines.extend(_section_metrics("Remove (`parser_remove_channel`)", rem))

    restore = payload.get("restore") or {}
    if restore:
        lines.append("## Восстановление очереди")
        lines.append("")
        lines.append(f"- restored: **{restore.get('restored', 0)}**")
        lines.append(f"- skipped_conflict: **{restore.get('skipped_conflict', 0)}**")
        lines.append(
            f"- skipped_not_cancelled: **{restore.get('skipped_not_cancelled', 0)}**"
        )
        conflicts = restore.get("conflicts") or []
        if conflicts:
            lines.append("")
            lines.append("Конфликты dedup (первые):")
            for c in conflicts[:20]:
                lines.append(
                    f"- id={c.get('id')} key=`{c.get('dedup_key')}` "
                    f"reason={c.get('reason')}"
                )
        lines.append("")

    sync = payload.get("sync_restore") or {}
    if sync:
        lines.append("## Восстановление синка")
        lines.append("")
        act = sync.get("n8n") or {}
        lines.append(f"- n8n activated: **{len(act.get('activated') or [])}**")
        if act.get("errors"):
            lines.append(f"- n8n errors: {act['errors']}")
        prod = sync.get("producers") or {}
        lines.append(f"- producers started: **{len(prod.get('started') or [])}**")
        lines.append("")

    foreign = payload.get("foreign_tasks_in_window") or {}
    if foreign:
        lines.append("## Внешние задачи в окне теста")
        lines.append("")
        lines.append(f"- total: **{foreign.get('total', 0)}**")
        for item in (foreign.get("by_source") or [])[:15]:
            lines.append(
                f"- `{item.get('task_type_code')}` / `{item.get('created_by')}`: "
                f"{item.get('count')}"
            )
        lines.append("")

    notes = payload.get("notes") or []
    if notes:
        lines.append("## Заметки")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _section_metrics(title: str, m: dict[str, Any]) -> list[str]:
    lines = [f"## {title}", ""]
    thr = m.get("throughput") or {}
    lines.append(f"- поставлено: **{m.get('enqueued', 0)}**")
    lines.append(f"- done: **{m.get('done', 0)}**")
    lines.append(f"- failed: **{m.get('failed', 0)}**")
    lines.append(f"- pending (осталось): **{m.get('pending', 0)}**")
    lines.append(f"- completion: **{m.get('completion_ratio', 0):.1%}**")
    lines.append(
        f"- скорость: **{thr.get('per_hour', 0)} задач/час** "
        f"({thr.get('per_minute', 0)} /мин) за {thr.get('elapsed_sec', 0)}s"
    )
    lat = m.get("latency") or {}
    if lat.get("count"):
        lines.append(
            f"- латентность done: avg={lat.get('avg_sec')}s "
            f"p50={lat.get('p50_sec')}s p95={lat.get('p95_sec')}s "
            f"(n={lat.get('count')})"
        )
    lines.append("")
    hourly = m.get("hourly_done") or []
    if hourly:
        lines.append("### Почасовая разборка (done)")
        lines.append("")
        lines.append("| hour (UTC) | done |")
        lines.append("|---|---:|")
        for h in hourly:
            lines.append(f"| {h.get('hour')} | {h.get('done')} |")
        lines.append("")
    accounts = m.get("per_account") or []
    if accounts:
        lines.append("### Per-account (top 20)")
        lines.append("")
        lines.append("| account | session | done | failed | pending | avg_lat_s |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for a in accounts[:20]:
            lines.append(
                f"| {a.get('account_id')} | {a.get('session_name')} | "
                f"{a.get('done')} | {a.get('failed')} | {a.get('pending')} | "
                f"{a.get('avg_latency_sec')} |"
            )
        lines.append("")
    errs = m.get("top_errors") or []
    if errs:
        lines.append("### Топ ошибок")
        lines.append("")
        for e in errs[:15]:
            lines.append(f"- ({e.get('count')}) `{e.get('error')}`")
        lines.append("")
    return lines


def write_interim_add_report(path: Path, metrics: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(_section_metrics("Add interim", metrics)) + "\n",
        encoding="utf-8",
    )
