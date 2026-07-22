"""Оркестратор фаз throughput-теста."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from loadtest.prod_e2e.api import DiscoveryApi
from loadtest.prod_e2e.db import normalize_ref
from loadtest.prod_e2e.errors import ErrorSink

from .config import Config
from .db import ThroughputDb
from .queue_swap import pause_queue, restore_queue
from .report import (
    append_timeline_row,
    build_phase_metrics,
    write_interim_add_report,
    write_reports,
)
from .state import RunState, StateStore
from .sync_control import (
    disable_external_sync,
    enable_external_sync,
    start_producers,
    stop_producers,
)

log = logging.getLogger("throughput.phases")

TIMELINE_FIELDS = [
    "ts",
    "phase",
    "done",
    "failed",
    "pending",
    "queued",
    "scheduled",
    "retry",
    "in_progress",
    "cancelled",
    "per_hour",
    "pickable",
    "busy",
    "note",
]


class PhaseOrchestrator:
    def __init__(
        self,
        cfg: Config,
        db: ThroughputDb,
        api: DiscoveryApi,
        errors: ErrorSink,
        state: RunState,
        store: StateStore,
        stop_event: asyncio.Event,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.api = api
        self.errors = errors
        self.state = state
        self.store = store
        self.stop = stop_event
        self.window_started_at: datetime | None = None

    def _save(self) -> None:
        self.store.save(self.state)

    def _should_skip(self, phase: str) -> bool:
        """При resume пропускаем уже завершённые фазы."""
        return phase in self.state.completed_phases

    async def run_all(self) -> None:
        if self.cfg.restore_only:
            log.info("=== restore-only mode ===")
            self.state.mark_phase("restore")
            self._save()
            await self.phase_restore()
            self.state.complete_phase("restore")
            # report мог уже быть — перепишем после успешного restore
            if "report" in self.state.completed_phases:
                self.state.completed_phases = [
                    p for p in self.state.completed_phases if p not in ("report", "done")
                ]
            self.state.mark_phase("report")
            await self.phase_report()
            self.state.complete_phase("report")
            self.state.mark_phase("done")
            self.state.complete_phase("done")
            self._save()
            return

        steps = [
            ("preflight", self.phase_preflight),
            ("sync_off", self.phase_sync_off),
            ("queue_swap", self.phase_queue_swap),
            ("wait_recovery", self.phase_wait_recovery),
            ("enqueue_add", self.phase_enqueue_add),
            ("monitor_add", self.phase_monitor_add),
            ("enqueue_remove", self.phase_enqueue_remove),
            ("monitor_remove", self.phase_monitor_remove),
            ("restore", self.phase_restore),
            ("report", self.phase_report),
        ]
        for name, fn in steps:
            if self.stop.is_set() and name not in ("restore", "report"):
                log.warning(
                    "Stop: пропускаем фазу %s, дальше только restore/report",
                    name,
                )
                continue
            if self._should_skip(name):
                log.info(
                    "Resume: skip completed phase %s (completed=%s)",
                    name,
                    self.state.completed_phases,
                )
                continue
            log.info("=== Phase %s ===", name)
            self.state.mark_phase(name)
            self._save()
            try:
                await fn()
                self.state.complete_phase(name)
            except Exception as exc:  # noqa: BLE001
                log.exception("Phase %s failed: %s", name, exc)
                self.errors.add(phase=name, op="phase", error=exc)
                if name not in ("restore", "report"):
                    # всё равно попробуем restore в finally main
                    raise
            self._save()

        if self.state.phase != "done":
            self.state.mark_phase("done")
            self.state.complete_phase("done")
            self._save()

    # --- phases ---

    async def phase_preflight(self) -> None:
        self.state.parser_id = self.cfg.parser_id
        # health
        health_ok = False
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{self.cfg.base_url}/health")
                health_ok = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="preflight", op="health", error=exc)
            log.warning("health check failed: %s", exc)

        parsers = await self.api.parser_list(phase="preflight")
        ids = [
            p.get("parser_id") or p.get("id")
            for p in parsers
            if isinstance(p, dict)
        ]
        if self.cfg.parser_id not in ids:
            raise RuntimeError(
                f"parser_id={self.cfg.parser_id!r} не найден в {self.cfg.base_url}"
                f"/discovery-api/parser/list (sample={ids[:10]}). "
                "Укажите реальный running clump id и --base-url http://127.0.0.1:8100 "
                "при запуске на vps-104."
            )
        if not health_ok:
            raise RuntimeError(
                f"GET {self.cfg.base_url}/health не вернул 200. "
                "На vps-104 используйте --base-url http://127.0.0.1:8100 "
                "(публичный домен с самого сервера даёт hairpin/503)."
            )

        overview = await self.db.accounts_overview()
        if overview.get("pickable", 0) <= 0:
            raise RuntimeError(
                "pickable_accounts=0. Разблокируйте аккаунты: "
                "psql \"$PGURL\" -v apply=1 -f scripts/ops_unlock_zombie_accounts.sql"
            )
        if overview.get("orphan", 0) > 0:
            log.warning(
                "orphan_account_locks=%s — рекомендуется ops_unlock_zombie_accounts.sql",
                overview["orphan"],
            )

        pool_limit = max(self.cfg.min_candidates * 2, self.cfg.add_count + 500)
        candidates = await self.db.fetch_candidates(
            platform_id=self.cfg.platform_id,
            limit=pool_limit,
            exclude_assigned=True,
        )
        if len(candidates) < self.cfg.min_candidates:
            # fallback: разрешить assigned
            candidates = await self.db.fetch_candidates(
                platform_id=self.cfg.platform_id,
                limit=pool_limit,
                exclude_assigned=False,
            )
        if len(candidates) < self.cfg.min_candidates:
            raise RuntimeError(
                f"Кандидатов каналов {len(candidates)} < {self.cfg.min_candidates} "
                f"(нужно {self.cfg.add_count}+10%)"
            )

        pauseable = await self.db.count_pauseable_tasks()
        result = {
            "health_ok": health_ok,
            "accounts": overview,
            "candidates": len(candidates),
            "pauseable_tasks": pauseable,
            "parser_ids_sample": ids[:20],
        }
        self.state.phase_results["preflight"] = result
        # кэш кандидатов на диск
        (self.cfg.run_dir / "candidates.json").write_text(
            json.dumps(
                [{"id": c.id, "ref": c.ref} for c in candidates],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("Preflight OK: %s", result)

    async def phase_sync_off(self) -> None:
        n8n_result = disable_external_sync(
            n8n_base_url=self.cfg.n8n_base_url,
            n8n_api_key=self.cfg.n8n_api_key or "",
            skip_n8n=self.cfg.skip_n8n,
            dry_run=self.cfg.dry_run,
        )
        self.state.n8n_deactivated = list(n8n_result.get("deactivated") or [])
        for err in n8n_result.get("errors") or []:
            self.errors.add(phase="sync_off", op="n8n_deactivate", error=err)

        prod = stop_producers(
            skip=self.cfg.skip_producers, dry_run=self.cfg.dry_run
        )
        self.state.producers_stopped = list(prod.get("stopped") or [])
        for err in prod.get("errors") or []:
            self.errors.add(phase="sync_off", op="stop_producers", error=err)

        self.state.phase_results["sync_off"] = {
            "n8n": n8n_result,
            "producers": prod,
        }
        self._save()
        log.info(
            "Sync off: n8n=%s producers=%s",
            len(self.state.n8n_deactivated),
            self.state.producers_stopped,
        )

    async def phase_queue_swap(self) -> None:
        backup_path = self.cfg.run_dir / self.state.queue_backup_file
        assert self.db.pool
        result = await pause_queue(
            self.db.pool,
            backup_path,
            run_id=self.cfg.run_id,
            dry_run=self.cfg.dry_run,
        )
        self.state.queue_paused_count = int(result.get("cancelled") or result.get("backed_up") or 0)
        self.state.needs_restore = True
        self.state.phase_results["queue_swap"] = result
        self._save()
        log.info("Queue swapped: %s", result)

    async def phase_wait_recovery(self) -> None:
        total = self.cfg.wait_recovery_sec
        if total <= 0:
            log.info("wait_recovery=0 — пропускаем")
            return
        log.info("Ожидание восстановления лимитов: %ss", total)
        csv_path = self.cfg.run_dir / "timeline_recovery.csv"
        started = time.monotonic()
        next_log = 0.0
        while not self.stop.is_set():
            elapsed = time.monotonic() - started
            if elapsed >= total:
                break
            if elapsed >= next_log:
                sample = await self.db.resource_summary_sample()
                append_timeline_row(
                    csv_path,
                    ["ts", "elapsed_sec", "sample_json"],
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "elapsed_sec": int(elapsed),
                        "sample_json": json.dumps(sample, ensure_ascii=False, default=str),
                    },
                )
                log.info("recovery sample t+%ss: %s", int(elapsed), sample)
                next_log = elapsed + self.cfg.recovery_log_interval_sec
            await asyncio.sleep(min(5.0, max(1.0, total - elapsed)))
        self.state.phase_results["wait_recovery"] = {
            "planned_sec": total,
            "elapsed_sec": min(total, time.monotonic() - started),
            "stopped_early": self.stop.is_set(),
        }

    async def phase_enqueue_add(self) -> None:
        if self.state.add_task_ids and len(self.state.added_channels) >= self.cfg.add_count:
            log.info(
                "Resume: add уже поставлены (%s channels, %s tasks)",
                len(self.state.added_channels),
                len(self.state.add_task_ids),
            )
            return

        candidates_path = self.cfg.run_dir / "candidates.json"
        if candidates_path.is_file():
            raw = json.loads(candidates_path.read_text(encoding="utf-8"))
            candidate_refs = [str(x["ref"]) for x in raw if x.get("ref")]
        else:
            pool = await self.db.fetch_candidates(
                platform_id=self.cfg.platform_id,
                limit=max(self.cfg.min_candidates * 2, self.cfg.add_count + 500),
                exclude_assigned=False,
            )
            candidate_refs = [c.ref for c in pool]

        target = self.cfg.add_count
        accepted_refs: list[str] = list(self.state.added_channels)
        accepted_task_ids: list[int] = list(self.state.add_task_ids)
        seen_norms = {normalize_ref(r) for r in accepted_refs}
        cursor = 0
        skipped_fatal = 0
        skipped_in_clump = 0
        http_errors = 0
        since = datetime.now(timezone.utc)

        while len(accepted_refs) < target and cursor < len(candidate_refs):
            if self.stop.is_set():
                break
            need = target - len(accepted_refs)
            chunk = []
            while cursor < len(candidate_refs) and len(chunk) < self.cfg.chunk_size:
                ref = candidate_refs[cursor]
                cursor += 1
                n = normalize_ref(ref)
                if not n or n in seen_norms:
                    continue
                seen_norms.add(n)
                chunk.append(ref)
            if not chunk:
                continue

            if self.cfg.dry_run:
                for ref in chunk:
                    if len(accepted_refs) >= target:
                        break
                    accepted_refs.append(ref)
                continue

            resp = await self.api.add_channels(
                self.cfg.parser_id,
                chunk,
                phase="enqueue_add",
            )
            if resp.get("_error"):
                http_errors += 1
                continue

            tids = [int(x) for x in (resp.get("task_ids") or [])]
            accepted_task_ids.extend(tids)

            # каналы, реально поставленные: из ответа или через SQL
            # API обычно возвращает task_ids; skipped_* — словари
            sf = resp.get("skipped_fatal") or {}
            sic = resp.get("skipped_in_clump") or {}
            skipped_fatal += len(sf) if isinstance(sf, dict) else 0
            skipped_in_clump += len(sic) if isinstance(sic, dict) else 0

            skipped_set = set()
            if isinstance(sf, dict):
                skipped_set.update(normalize_ref(k) for k in sf)
            if isinstance(sic, dict):
                skipped_set.update(normalize_ref(k) for k in sic)

            for ref in chunk:
                if normalize_ref(ref) in skipped_set:
                    continue
                accepted_refs.append(ref)
                if len(accepted_refs) >= target:
                    break

            # периодический сейв
            self.state.added_channels = accepted_refs[:target]
            self.state.add_task_ids = accepted_task_ids
            self._save()

        # Источник истины — PG с фильтром created_at >= since.
        # HTTP task_ids могут включать dedup-hit уже done/старых задач
        # (_task_id_from_enqueue возвращает existing_task_id) → раздувает done.
        if not self.cfg.dry_run and accepted_refs:
            found = await self.db.find_task_ids_for_channels(
                parser_id=self.cfg.parser_id,
                channel_refs=accepted_refs[:target],
                task_type="parser_add_channel",
                since=since,
            )
            if found:
                accepted_task_ids = found
            elif accepted_task_ids:
                accepted_task_ids = await self.db.filter_task_ids_created_since(
                    accepted_task_ids, since=since
                )

        self.state.added_channels = accepted_refs[:target]
        self.state.add_task_ids = accepted_task_ids
        result = {
            "requested": target,
            "accepted_channels": len(self.state.added_channels),
            "task_ids": len(self.state.add_task_ids),
            "skipped_fatal": skipped_fatal,
            "skipped_in_clump": skipped_in_clump,
            "http_errors": http_errors,
            "candidates_scanned": cursor,
            "dry_run": self.cfg.dry_run,
        }
        self.state.phase_results["enqueue_add"] = result
        (self.cfg.run_dir / "added_channels.json").write_text(
            json.dumps(
                {
                    "channels": self.state.added_channels,
                    "task_ids": self.state.add_task_ids,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._save()
        log.info("Enqueue add: %s", result)
        if len(self.state.added_channels) < target and not self.cfg.dry_run:
            log.warning(
                "Поставлено только %s/%s каналов (мало кандидатов или много skip)",
                len(self.state.added_channels),
                target,
            )

    async def phase_monitor_add(self) -> None:
        metrics = await self._monitor_window(
            phase="monitor_add",
            task_ids=self.state.add_task_ids,
            window_sec=self.cfg.add_window_sec,
            csv_name="timeline_add.csv",
            early_exit_terminal=True,
        )
        self.state.phase_results["monitor_add"] = metrics
        write_interim_add_report(self.cfg.run_dir / "report_add.md", metrics)
        self._save()

    async def phase_enqueue_remove(self) -> None:
        channels = list(self.state.added_channels)
        if not channels:
            path = self.cfg.run_dir / "added_channels.json"
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                channels = list(raw.get("channels") or [])
                self.state.added_channels = channels

        if self.state.remove_task_ids:
            log.info(
                "Resume: remove уже поставлены (%s tasks)",
                len(self.state.remove_task_ids),
            )
            return

        since = datetime.now(timezone.utc)
        task_ids: list[int] = []
        http_errors = 0
        enqueued_channels = 0

        for i in range(0, len(channels), self.cfg.chunk_size):
            if self.stop.is_set():
                break
            chunk = channels[i : i + self.cfg.chunk_size]
            if self.cfg.dry_run:
                enqueued_channels += len(chunk)
                continue
            resp = await self.api.remove_channels(
                self.cfg.parser_id,
                chunk,
                phase="enqueue_remove",
            )
            if resp.get("_error"):
                http_errors += 1
                continue
            tids = [int(x) for x in (resp.get("task_ids") or [])]
            task_ids.extend(tids)
            enqueued_channels += len(tids) if tids else 0
            self.state.remove_task_ids = task_ids
            self._save()

        if not self.cfg.dry_run and channels:
            found = await self.db.find_task_ids_for_channels(
                parser_id=self.cfg.parser_id,
                channel_refs=channels,
                task_type="parser_remove_channel",
                since=since,
            )
            if found:
                task_ids = found
            elif task_ids:
                task_ids = await self.db.filter_task_ids_created_since(
                    task_ids, since=since
                )

        self.state.remove_task_ids = task_ids
        result = {
            "source_channels": len(channels),
            "task_ids": len(task_ids),
            "enqueued_hint": enqueued_channels,
            "http_errors": http_errors,
            "dry_run": self.cfg.dry_run,
            "note": (
                "Каналы, не попавшие в clump (add не успел), "
                "producer пропускает — это ожидаемо"
            ),
        }
        self.state.phase_results["enqueue_remove"] = result
        (self.cfg.run_dir / "remove_tasks.json").write_text(
            json.dumps(result | {"task_ids": task_ids}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._save()
        log.info("Enqueue remove: %s", result)

    async def phase_monitor_remove(self) -> None:
        metrics = await self._monitor_window(
            phase="monitor_remove",
            task_ids=self.state.remove_task_ids,
            window_sec=self.cfg.remove_window_sec,
            csv_name="timeline_remove.csv",
            early_exit_terminal=False,  # по плану — ровно 2 часа, остаток остаётся
        )
        self.state.phase_results["monitor_remove"] = metrics
        self._save()

    async def phase_restore(self) -> None:
        """Восстановить очередь + n8n + producers. Remove-остаток не трогаем."""
        backup_path = self.cfg.run_dir / self.state.queue_backup_file
        assert self.db.pool
        restore_result = await restore_queue(
            self.db.pool,
            backup_path,
            run_id=self.cfg.run_id,
            dry_run=self.cfg.dry_run,
        )
        self.state.restore_result = restore_result

        n8n = enable_external_sync(
            n8n_base_url=self.cfg.n8n_base_url,
            n8n_api_key=self.cfg.n8n_api_key or "",
            workflows=self.state.n8n_deactivated,
            skip_n8n=self.cfg.skip_n8n,
            dry_run=self.cfg.dry_run,
        )
        producers = start_producers(
            self.state.producers_stopped, dry_run=self.cfg.dry_run
        )
        self.state.phase_results["restore"] = {
            "queue": restore_result,
            "n8n": n8n,
            "producers": producers,
            "remove_left_in_queue": True,
        }
        self.state.needs_restore = False
        self._save()
        log.info("Restore done: queue=%s n8n=%s", restore_result, n8n)

    async def phase_report(self) -> None:
        add_m = self.state.phase_results.get("monitor_add")
        rem_m = self.state.phase_results.get("monitor_remove")
        restore = self.state.phase_results.get("restore") or {}
        foreign = None
        # окно: от sync_off/queue_swap до сейчас
        try:
            ts_map = self.state.phase_timestamps
            start_key = "queue_swap" if "queue_swap" in ts_map else "sync_off"
            if start_key in ts_map:
                since = datetime.fromisoformat(ts_map[start_key])
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                foreign = await self.db.foreign_tasks_in_window(
                    since=since,
                    exclude_task_ids=(
                        list(self.state.add_task_ids) + list(self.state.remove_task_ids)
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="report", op="foreign_tasks", error=exc)

        cfg_snap = {
            "add_count": self.cfg.add_count,
            "wait_recovery_sec": self.cfg.wait_recovery_sec,
            "add_window_sec": self.cfg.add_window_sec,
            "remove_window_sec": self.cfg.remove_window_sec,
            "chunk_size": self.cfg.chunk_size,
            "base_url": self.cfg.base_url,
            "dry_run": self.cfg.dry_run,
            "skip_n8n": self.cfg.skip_n8n,
        }
        notes = [
            "Оставшиеся parser_remove_channel после окна 2ч намеренно "
            "оставлены в очереди и участвуют в восстановленном продакшн-потоке.",
            f"Ошибок harness: {self.errors.count()}",
        ]
        md, js = write_reports(
            run_dir=self.cfg.run_dir,
            run_id=self.cfg.run_id,
            parser_id=self.cfg.parser_id,
            config_snapshot=cfg_snap,
            add_metrics=add_m,
            remove_metrics=rem_m,
            restore_result=restore.get("queue") or self.state.restore_result,
            sync_restore={"n8n": restore.get("n8n"), "producers": restore.get("producers")},
            foreign_tasks=foreign,
            phase_timestamps=self.state.phase_timestamps,
            notes=notes,
        )
        self.state.phase_results["report"] = {"md": str(md), "json": str(js)}
        log.info("Report written: %s / %s", md, js)

    # --- monitoring helper ---

    async def _monitor_window(
        self,
        *,
        phase: str,
        task_ids: list[int],
        window_sec: int,
        csv_name: str,
        early_exit_terminal: bool,
    ) -> dict[str, Any]:
        csv_path = self.cfg.run_dir / csv_name
        started_mono = time.monotonic()
        started_at = datetime.now(timezone.utc)
        overview = await self.db.accounts_overview()

        if self.cfg.dry_run or not task_ids:
            status_counts = {"done": 0, "queued": len(task_ids)}
            elapsed = 0.0
            metrics = build_phase_metrics(
                label=phase,
                enqueued=len(task_ids),
                status_counts=status_counts,
                window_sec=float(window_sec),
                elapsed_sec=elapsed,
                latency={"count": 0},
                hourly=[],
                per_account=[],
                errors=[],
            )
            metrics["accounts_overview"] = overview
            return metrics

        while not self.stop.is_set():
            elapsed = time.monotonic() - started_mono
            if elapsed >= window_sec:
                break

            status_counts = await self.db.task_status_counts(task_ids)
            done = int(status_counts.get("done", 0))
            failed = int(status_counts.get("failed", 0))
            cancelled = int(status_counts.get("cancelled", 0))
            pending = sum(
                int(status_counts.get(s, 0))
                for s in ("queued", "scheduled", "retry", "in_progress", "stuck")
            )
            speed = (
                (done * 3600.0 / elapsed) if elapsed > 1 else 0.0
            )
            try:
                overview = await self.db.accounts_overview()
            except Exception as exc:  # noqa: BLE001
                self.errors.add(phase=phase, op="accounts_overview", error=exc)
                overview = {}

            append_timeline_row(
                csv_path,
                TIMELINE_FIELDS,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "phase": phase,
                    "done": done,
                    "failed": failed,
                    "pending": pending,
                    "queued": status_counts.get("queued", 0),
                    "scheduled": status_counts.get("scheduled", 0),
                    "retry": status_counts.get("retry", 0),
                    "in_progress": status_counts.get("in_progress", 0),
                    "cancelled": cancelled,
                    "per_hour": round(speed, 2),
                    "pickable": overview.get("pickable"),
                    "busy": overview.get("busy"),
                    "note": "",
                },
            )
            log.info(
                "%s t+%ss done=%s pending=%s failed=%s ~%.1f/h",
                phase,
                int(elapsed),
                done,
                pending,
                failed,
                speed,
            )

            if early_exit_terminal:
                terminal = done + failed + cancelled
                if terminal >= len(task_ids) and pending == 0:
                    log.info("%s: все задачи терминальны — досрочный выход", phase)
                    break

            await asyncio.sleep(self.cfg.sampler_interval_sec)

        elapsed_final = min(float(window_sec), time.monotonic() - started_mono)
        status_counts = await self.db.task_status_counts(task_ids)
        latency = await self.db.latency_stats(task_ids)
        # Почасово по всем done этих task_ids (без отсечения по старту
        # монитора — иначе теряются done, завершённые во время enqueue).
        hourly = await self.db.hourly_done_counts(task_ids, since=None)
        per_account = await self.db.per_account_stats(task_ids)
        errors = await self.db.error_breakdown(task_ids)
        metrics = build_phase_metrics(
            label=phase,
            enqueued=len(task_ids),
            status_counts=status_counts,
            window_sec=float(window_sec),
            elapsed_sec=elapsed_final,
            latency=latency,
            hourly=hourly,
            per_account=per_account,
            errors=errors,
        )
        metrics["accounts_overview"] = overview
        metrics["started_at"] = started_at.isoformat()
        metrics["finished_at"] = datetime.now(timezone.utc).isoformat()
        return metrics


async def ensure_restore_on_exit(
    orch: PhaseOrchestrator,
) -> None:
    """Если очередь была подменена — восстановить даже при аварии."""
    if not orch.state.needs_restore:
        return
    if orch.state.phase in ("restore", "report", "done") and orch.state.restore_result:
        return
    log.warning("Emergency restore (needs_restore=True, phase=%s)", orch.state.phase)
    try:
        orch.state.mark_phase("restore")
        orch._save()
        await orch.phase_restore()
    except Exception as exc:  # noqa: BLE001
        log.exception("Emergency restore failed: %s", exc)
        orch.errors.add(phase="restore", op="emergency", error=exc)
