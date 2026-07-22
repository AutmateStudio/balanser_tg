"""Сборка отчёта Markdown + JSON."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import ErrorRecord


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def build_report(
    *,
    cfg: Config,
    phase_results: dict[str, Any],
    runtime_summary: dict[str, Any],
    errors: list[ErrorRecord],
) -> tuple[Path, Path]:
    run_dir = cfg.run_dir
    speed_rows = _read_csv(run_dir / "speed_add.csv")
    metrics_rows = _read_csv(run_dir / "metrics_timeline.csv")

    done_1m_vals = [x for x in (_f(r.get("done_1m")) for r in speed_rows) if x is not None]
    speed_stats = {
        "samples": len(speed_rows),
        "done_1m_avg": statistics.mean(done_1m_vals) if done_1m_vals else None,
        "done_1m_peak": max(done_1m_vals) if done_1m_vals else None,
        "final": speed_rows[-1] if speed_rows else {},
    }

    phase_b = phase_results.get("B") or {}
    phase_cd = phase_results.get("CD") or {}
    phase_e = phase_results.get("E") or {}
    phase_a = phase_results.get("A") or {}

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": cfg.run_id,
        "stand": cfg.base_url,
        "scale": cfg.scale.label,
        "seed": cfg.seed,
        "parser_id": cfg.parser_id,
        "owner_user_id": cfg.owner_user_id,
        "durations": {
            "enqueue_check_after_sec": cfg.enqueue_check_after_sec,
            "change_phase_duration_sec": cfg.change_phase_duration_sec,
            "final_collect_sec": cfg.final_collect_sec,
            "total_sec": cfg.total_duration_sec,
        },
        "section_1_summary": {
            "users": cfg.scale.users,
            "channels_per_user": cfg.scale.channels_per_user,
            "shared_ratio": cfg.scale.shared_ratio,
            "dry_run": cfg.dry_run,
            "started_at": runtime_summary.get("started_at"),
        },
        "section_2_enqueue": phase_b,
        "section_3_add_speed": {
            **speed_stats,
            "phase_a": phase_a,
            "add_latency": phase_cd.get("add_latency"),
        },
        "section_4_changes": {
            "change_ops_count": phase_cd.get("change_ops_count"),
            "remove_latency": phase_cd.get("remove_latency"),
        },
        "section_5_per_account": phase_e.get("per_account") or [],
        "section_6_pipeline": phase_e.get("pipeline") or {},
        "section_7_errors": [
            {
                "ts": e.ts,
                "phase": e.phase,
                "user_key": e.user_key,
                "op": e.op,
                "error": e.error,
            }
            for e in errors
        ],
        "metrics_samples": len(metrics_rows),
        "pickable_watchdog_notes": [
            e for e in errors if e.op == "pickable_watchdog"
        ],
    }

    json_path = run_dir / "report.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    md = _to_markdown(report, cfg)
    md_path = run_dir / "report.md"
    md_path.write_text(md, encoding="utf-8")
    return md_path, json_path


def _to_markdown(report: dict[str, Any], cfg: Config) -> str:
    lines: list[str] = []
    lines.append(f"# Отчёт loadtest {report['run_id']}")
    lines.append("")
    lines.append("## 1. Сводка прогона")
    lines.append("")
    lines.append(f"- Стенд: `{report['stand']}`")
    lines.append(f"- Scale: **{report['scale']}** (seed={report['seed']})")
    lines.append(f"- parser_id: `{report['parser_id']}`")
    lines.append(f"- owner_user_id: `{report['owner_user_id']}`")
    lines.append(
        f"- Длительности: check={cfg.enqueue_check_after_sec}s, "
        f"change={cfg.change_phase_duration_sec}s, final={cfg.final_collect_sec}s"
    )
    lines.append(f"- Старт: {report['section_1_summary'].get('started_at')}")
    lines.append(f"- dry_run: {report['section_1_summary'].get('dry_run')}")
    lines.append("")

    lines.append("## 2. Полнота enqueue (add)")
    lines.append("")
    b = report.get("section_2_enqueue") or {}
    g = b.get("global") or {}
    if g:
        lines.append(
            f"- Ожидалось refs: **{g.get('expected')}**, найдено задач: **{g.get('found_tasks')}**, "
            f"present_refs: **{g.get('present_refs')}**, missing: **{g.get('missing_count')}**"
        )
        lines.append(f"- Completeness: **{g.get('completeness')}**")
        lines.append(f"- Status counts: `{g.get('status_counts')}`")
        missing = g.get("missing_refs") or []
        if missing:
            lines.append(f"- Missing sample (до 20): `{missing[:20]}`")
    else:
        lines.append("- Нет данных Phase B")
    lines.append("")

    lines.append("## 3. Скорость добавления каналов")
    lines.append("")
    s3 = report.get("section_3_add_speed") or {}
    lines.append(f"- Samples speed_add.csv: {s3.get('samples')}")
    lines.append(f"- done/мин среднее: **{s3.get('done_1m_avg')}**, пик: **{s3.get('done_1m_peak')}**")
    lines.append(f"- Финальный снимок: `{s3.get('final')}`")
    lines.append(f"- Latency add (apply): `{s3.get('add_latency')}`")
    lines.append("")

    lines.append("## 4. Изменения (remove/disable/add)")
    lines.append("")
    s4 = report.get("section_4_changes") or {}
    lines.append(f"- Операций change: **{s4.get('change_ops_count')}**")
    lines.append(f"- Latency remove: `{s4.get('remove_latency')}`")
    lines.append("")

    lines.append("## 5. Per-account: сообщения / L2 / лиды")
    lines.append("")
    rows = report.get("section_5_per_account") or []
    if not rows:
        lines.append("_Нет данных (возможно, за 2 часа не было ingest на assigned каналы)._")
    else:
        lines.append(
            "| account | session | messages | L2 total | L2 leads | L2 filtered | ttf lead, s |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for r in rows:
            lines.append(
                f"| {r.get('account_id')} | {r.get('session_name')} | "
                f"{r.get('messages_total')} | {r.get('l2_total')} | {r.get('l2_leads')} | "
                f"{r.get('l2_filtered')} | {r.get('time_to_first_lead_sec')} |"
            )
    lines.append("")

    lines.append("## 6. Итоги пайплайна")
    lines.append("")
    p = report.get("section_6_pipeline") or {}
    for k, v in p.items():
        lines.append(f"- `{k}`: **{v}**")
    if not p:
        lines.append("- Нет данных")
    lines.append("")

    lines.append("## 7. Ошибки / сбои")
    lines.append("")
    errs = report.get("section_7_errors") or []
    lines.append(f"Всего зафиксировано: **{len(errs)}** (тест не прерывался).")
    lines.append("")
    if errs:
        lines.append("| ts | phase | user | op | error |")
        lines.append("|---|---|---|---|---|")
        for e in errs[:200]:
            err = str(e.get("error", "")).replace("|", "\\|")[:200]
            lines.append(
                f"| {e.get('ts')} | {e.get('phase')} | {e.get('user_key')} | "
                f"{e.get('op')} | {err} |"
            )
        if len(errs) > 200:
            lines.append(f"\n_… ещё {len(errs) - 200} в report.json / errors.jsonl_")
    lines.append("")
    lines.append("---")
    lines.append(f"Сырые файлы: `{cfg.run_dir}`")
    return "\n".join(lines) + "\n"
