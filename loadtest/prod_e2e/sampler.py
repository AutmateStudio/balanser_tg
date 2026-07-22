"""Фоновый сэмплер metrics + watchdog pickable=0."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import DiscoveryApi
from .config import Config
from .errors import ErrorSink

log = logging.getLogger("loadtest.sampler")


class MetricsSampler:
    def __init__(
        self,
        cfg: Config,
        api: DiscoveryApi,
        errors: ErrorSink,
        stop_event: asyncio.Event,
    ) -> None:
        self.cfg = cfg
        self.api = api
        self.errors = errors
        self.stop = stop_event
        self.csv_path = cfg.run_dir / "metrics_timeline.csv"
        self.jsonl_path = cfg.run_dir / "metrics.jsonl"
        self.pickable_zero_since: float | None = None
        self.pickable_warnings = 0
        self._fieldnames = [
            "ts",
            "pickable",
            "busy",
            "active",
            "stuck_count",
            "oldest_queued_age_seconds",
            "done_last_5_min",
            "enqueued_last_5_min",
            "add_queued",
            "add_in_progress",
            "add_done_hint",
            "remove_queued",
        ]
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self._fieldnames).writeheader()
        self.jsonl_path.write_text("", encoding="utf-8")

    async def run(self) -> None:
        while not self.stop.is_set():
            try:
                raw = await self.api.queue_metrics(phase="sampler")
                row = self._flatten(raw)
                with self.csv_path.open("a", newline="", encoding="utf-8") as f:
                    csv.DictWriter(f, fieldnames=self._fieldnames).writerow(row)
                with self.jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {"ts": row["ts"], "raw": raw},
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
                self._watch_pickable(row)
            except Exception as exc:  # noqa: BLE001
                self.errors.add(phase="sampler", op="queue_metrics", error=exc)
            await asyncio.sleep(self.cfg.sampler_interval_sec)

    def _flatten(self, raw: dict[str, Any]) -> dict[str, Any]:
        q = raw.get("queue") or {}
        a = raw.get("accounts") or {}
        flow = q.get("flow") or {}
        by_type = q.get("by_type") or {}
        add = by_type.get("parser_add_channel") or {}
        rem = by_type.get("parser_remove_channel") or {}
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pickable": a.get("pickable") if a.get("pickable") is not None else a.get("active"),
            "busy": a.get("busy"),
            "active": a.get("active"),
            "stuck_count": q.get("stuck_count"),
            "oldest_queued_age_seconds": q.get("oldest_queued_age_seconds"),
            "done_last_5_min": flow.get("done_last_5_min") or q.get("done_last_5_min"),
            "enqueued_last_5_min": flow.get("enqueued_last_5_min"),
            "add_queued": add.get("queued") or add.get("scheduled"),
            "add_in_progress": add.get("in_progress"),
            "add_done_hint": add.get("done"),
            "remove_queued": rem.get("queued") or rem.get("scheduled"),
        }

    def _watch_pickable(self, row: dict[str, Any]) -> None:
        pick = row.get("pickable")
        try:
            pick_n = int(pick) if pick is not None else None
        except (TypeError, ValueError):
            pick_n = None
        now = time.monotonic()
        if pick_n == 0:
            if self.pickable_zero_since is None:
                self.pickable_zero_since = now
            elif now - self.pickable_zero_since >= self.cfg.pickable_zero_warn_sec:
                self.pickable_warnings += 1
                self.errors.add(
                    phase="sampler",
                    op="pickable_watchdog",
                    error=f"pickable=0 дольше {self.cfg.pickable_zero_warn_sec}s",
                    detail={"busy": row.get("busy"), "warnings": self.pickable_warnings},
                )
                # сброс таймера, чтобы не спамить каждую секунду
                self.pickable_zero_since = now
        else:
            self.pickable_zero_since = None
