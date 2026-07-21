"""Фазы A–E нагрузочного теста."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import DiscoveryApi
from .config import Config
from .db import Db, normalize_ref
from .errors import ErrorSink
from .seed import SeedResult, UserPlan

log = logging.getLogger("loadtest.phases")


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass
class UserRuntime:
    plan: UserPlan
    add_task_ids: list[int] = field(default_factory=list)
    add_action_ids: list[str] = field(default_factory=list)
    remove_task_ids: list[int] = field(default_factory=list)
    api_responses: list[dict[str, Any]] = field(default_factory=list)
    active_refs: list[str] = field(default_factory=list)
    enqueue_verify: dict[str, Any] = field(default_factory=dict)
    change_ops: list[dict[str, Any]] = field(default_factory=list)


class PhaseRunner:
    def __init__(
        self,
        cfg: Config,
        api: DiscoveryApi,
        db: Db,
        seed: SeedResult,
        errors: ErrorSink,
        stop_event: asyncio.Event,
    ) -> None:
        self.cfg = cfg
        self.api = api
        self.db = db
        self.seed = seed
        self.errors = errors
        self.stop = stop_event
        self.started_at = datetime.now(timezone.utc)
        self.runtimes = [UserRuntime(plan=u, active_refs=list(u.refs)) for u in seed.users]
        self.phase_results: dict[str, Any] = {}

    def _dump(self, name: str, data: Any) -> Path:
        path = self.cfg.run_dir / name
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    # ---------- Phase A: batch add ----------
    async def phase_a_batch_add(self) -> dict[str, Any]:
        log.info("Phase A: batch add scale=%s", self.cfg.scale.label)
        if self.cfg.dry_run:
            summary = {
                "dry_run": True,
                "users": len(self.runtimes),
                "channels_per_user": self.cfg.scale.channels_per_user,
            }
            self.phase_results["A"] = summary
            self._dump("phase_a.json", summary)
            return summary

        async def one_user(rt: UserRuntime) -> None:
            if self.stop.is_set():
                return
            for chunk in _chunks(rt.plan.refs, self.cfg.chunk_size):
                if self.stop.is_set():
                    return
                try:
                    resp = await self.api.add_channels(
                        self.cfg.parser_id,
                        chunk,
                        phase="A",
                        user_key=rt.plan.project_name,
                    )
                    rt.api_responses.append({"op": "add", "chunk": chunk, "resp": resp})
                    if resp.get("_error"):
                        continue
                    tids = resp.get("task_ids") or []
                    rt.add_task_ids.extend(int(x) for x in tids)
                    if resp.get("action_id"):
                        rt.add_action_ids.append(str(resp["action_id"]))
                except Exception as exc:  # noqa: BLE001
                    self.errors.add(
                        phase="A",
                        op="add-channels",
                        error=exc,
                        user_key=rt.plan.project_name,
                        detail={"chunk_size": len(chunk)},
                    )

        await asyncio.gather(*(one_user(rt) for rt in self.runtimes))
        summary = {
            "users": len(self.runtimes),
            "total_task_ids": sum(len(rt.add_task_ids) for rt in self.runtimes),
            "per_user": [
                {
                    "project": rt.plan.project_name,
                    "project_id": rt.plan.project_id,
                    "refs": len(rt.plan.refs),
                    "task_ids": len(rt.add_task_ids),
                    "action_ids": rt.add_action_ids,
                }
                for rt in self.runtimes
            ],
        }
        self.phase_results["A"] = summary
        self._dump("phase_a.json", summary)
        log.info("Phase A done: task_ids=%s", summary["total_task_ids"])
        return summary

    # ---------- Phase B: enqueue check at t10 ----------
    async def phase_b_enqueue_check(self) -> dict[str, Any]:
        log.info(
            "Phase B: wait %ss then verify enqueue",
            self.cfg.enqueue_check_after_sec,
        )
        await self._sleep_interruptible(self.cfg.enqueue_check_after_sec)
        if self.cfg.dry_run:
            summary = {"dry_run": True}
            self.phase_results["B"] = summary
            return summary

        per_user: list[dict[str, Any]] = []
        for rt in self.runtimes:
            try:
                ver = await self.db.verify_enqueue(
                    parser_id=self.cfg.parser_id,
                    expected_refs=rt.plan.refs,
                    since=self.started_at,
                    task_type="parser_add_channel",
                )
                rt.enqueue_verify = ver
                per_user.append({"project": rt.plan.project_name, **ver})
            except Exception as exc:  # noqa: BLE001
                self.errors.add(
                    phase="B",
                    op="verify_enqueue",
                    error=exc,
                    user_key=rt.plan.project_name,
                )
                per_user.append({"project": rt.plan.project_name, "error": str(exc)})

        # глобальная сверка по union refs (с учётом пересечения 20%)
        all_refs = sorted({normalize_ref(r) for rt in self.runtimes for r in rt.plan.refs})
        try:
            global_ver = await self.db.verify_enqueue(
                parser_id=self.cfg.parser_id,
                expected_refs=all_refs,
                since=self.started_at,
                task_type="parser_add_channel",
            )
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="B", op="verify_enqueue_global", error=exc)
            global_ver = {"error": str(exc)}

        summary = {
            "waited_sec": self.cfg.enqueue_check_after_sec,
            "global": global_ver,
            "per_user": per_user,
        }
        self.phase_results["B"] = summary
        self._dump("phase_b_enqueue.json", summary)
        log.info(
            "Phase B: global completeness=%s missing=%s",
            global_ver.get("completeness"),
            global_ver.get("missing_count"),
        )
        return summary

    # ---------- Phase C+D: speed + random changes ----------
    async def phase_cd_speed_and_changes(self) -> dict[str, Any]:
        duration = self.cfg.change_phase_duration_sec
        log.info("Phase C+D: speed + changes for %ss", duration)
        speed_path = self.cfg.run_dir / "speed_add.csv"
        with speed_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "ts",
                    "backlog",
                    "done_1m",
                    "done_5m",
                    "done_since_start",
                    "insuff_5m",
                    "oldest_queued_age_sec",
                    "pickable",
                    "busy",
                ],
            )
            w.writeheader()

        change_path = self.cfg.run_dir / "changes.jsonl"
        change_path.write_text("", encoding="utf-8")

        end = time.monotonic() + duration
        rng = random.Random(self.cfg.seed + 7)
        leftover_refs = self._leftover_refs(rng)

        async def speed_loop() -> None:
            while time.monotonic() < end and not self.stop.is_set():
                try:
                    snap = await self.db.add_speed_snapshot(
                        parser_id=self.cfg.parser_id, since=self.started_at
                    )
                    with speed_path.open("a", newline="", encoding="utf-8") as f:
                        csv.DictWriter(f, fieldnames=list(snap.keys())).writerow(snap)
                except Exception as exc:  # noqa: BLE001
                    self.errors.add(phase="C", op="speed_snapshot", error=exc)
                await self._sleep_interruptible(self.cfg.sampler_interval_sec)

        async def change_loop() -> None:
            if self.cfg.dry_run:
                return
            while time.monotonic() < end and not self.stop.is_set():
                for rt in self.runtimes:
                    if self.stop.is_set() or time.monotonic() >= end:
                        break
                    try:
                        op = await self._random_change(rt, rng, leftover_refs)
                        if op:
                            rt.change_ops.append(op)
                            with change_path.open("a", encoding="utf-8") as f:
                                f.write(json.dumps(op, ensure_ascii=False, default=str) + "\n")
                    except Exception as exc:  # noqa: BLE001
                        self.errors.add(
                            phase="D",
                            op="random_change",
                            error=exc,
                            user_key=rt.plan.project_name,
                        )
                await self._sleep_interruptible(self.cfg.change_interval_sec)

        await asyncio.gather(speed_loop(), change_loop())

        remove_lat = await self.db.change_task_latency(
            parser_id=self.cfg.parser_id,
            since=self.started_at,
            task_type="parser_remove_channel",
        )
        add_lat = await self.db.change_task_latency(
            parser_id=self.cfg.parser_id,
            since=self.started_at,
            task_type="parser_add_channel",
        )
        summary = {
            "duration_sec": duration,
            "speed_csv": str(speed_path),
            "changes_jsonl": str(change_path),
            "change_ops_count": sum(len(rt.change_ops) for rt in self.runtimes),
            "remove_latency": remove_lat,
            "add_latency": add_lat,
        }
        self.phase_results["CD"] = summary
        self._dump("phase_cd.json", summary)
        return summary

    def _leftover_refs(self, rng: random.Random) -> list[str]:
        used = {normalize_ref(r) for rt in self.runtimes for r in rt.plan.refs}
        # из shared+unique seed нет leftover в памяти — берём из seed.shared не нужно;
        # для Phase D extra-add используем refs из других пользователей как «новые»
        # лучше: сгенерируем пул из всех уникальных, которых нет у конкретного юзера
        all_unique = []
        for rt in self.runtimes:
            all_unique.extend(rt.plan.unique_refs)
        # доп. refs: нормализованные unique других юзеров
        pool = [r for r in all_unique if normalize_ref(r) not in used]
        # если пусто — используем unique из всех как candidates для cross-add
        if not pool:
            pool = list({r for rt in self.runtimes for r in rt.plan.unique_refs})
        rng.shuffle(pool)
        return pool

    async def _random_change(
        self,
        rt: UserRuntime,
        rng: random.Random,
        leftover: list[str],
    ) -> dict[str, Any] | None:
        choice = rng.random()
        ts = datetime.now(timezone.utc).isoformat()
        active = list(rt.active_refs)
        if not active:
            return None

        # remove
        if choice < 0.40 and active:
            n = max(1, int(len(active) * self.cfg.change_remove_fraction))
            n = min(n, 5, len(active))
            picked = rng.sample(active, n)
            t0 = time.monotonic()
            resp = await self.api.remove_channels(
                self.cfg.parser_id,
                picked,
                phase="D",
                user_key=rt.plan.project_name,
            )
            enqueue_ms = (time.monotonic() - t0) * 1000
            tids = []
            if not resp.get("_error"):
                tids = [int(x) for x in (resp.get("task_ids") or [])]
                rt.remove_task_ids.extend(tids)
                for r in picked:
                    if r in rt.active_refs:
                        rt.active_refs.remove(r)
            return {
                "ts": ts,
                "project": rt.plan.project_name,
                "op": "remove",
                "refs": picked,
                "task_ids": tids,
                "enqueue_latency_ms": enqueue_ms,
                "resp_error": bool(resp.get("_error")),
            }

        # disable via SQL
        if choice < 0.70 and active:
            n = max(1, int(len(active) * self.cfg.change_disable_fraction))
            n = min(n, 5, len(active))
            picked_refs = rng.sample(active, n)
            id_by_ref = {c["ref"]: c["id"] for c in rt.plan.channels}
            cids = [id_by_ref[r] for r in picked_refs if r in id_by_ref]
            t0 = time.monotonic()
            updated = await self.db.set_links_enabled(
                project_id=rt.plan.project_id, channel_ids=cids, enabled=False
            )
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "ts": ts,
                "project": rt.plan.project_name,
                "op": "disable",
                "refs": picked_refs,
                "channel_ids": cids,
                "updated": updated,
                "enqueue_latency_ms": latency_ms,
            }

        # extra add
        n = min(self.cfg.change_add_extra, 3)
        if leftover:
            picked = []
            for _ in range(n):
                if not leftover:
                    break
                picked.append(leftover.pop())
        else:
            # добавить unique другого пользователя
            others = [
                r
                for o in self.runtimes
                if o is not rt
                for r in o.plan.unique_refs
                if r not in rt.active_refs
            ]
            if not others:
                return None
            picked = rng.sample(others, min(n, len(others)))

        # link to project in DB then enqueue
        # channel ids may be unknown for cross-user — skip SQL link, only API add
        t0 = time.monotonic()
        resp = await self.api.add_channels(
            self.cfg.parser_id,
            picked,
            phase="D",
            user_key=rt.plan.project_name,
        )
        enqueue_ms = (time.monotonic() - t0) * 1000
        tids = []
        if not resp.get("_error"):
            tids = [int(x) for x in (resp.get("task_ids") or [])]
            rt.add_task_ids.extend(tids)
            for r in picked:
                if r not in rt.active_refs:
                    rt.active_refs.append(r)
        return {
            "ts": ts,
            "project": rt.plan.project_name,
            "op": "add_extra",
            "refs": picked,
            "task_ids": tids,
            "enqueue_latency_ms": enqueue_ms,
            "resp_error": bool(resp.get("_error")),
        }

    # ---------- Phase E: final collect ----------
    async def phase_e_final_collect(self) -> dict[str, Any]:
        log.info("Phase E: final collect %ss", self.cfg.final_collect_sec)
        await self._sleep_interruptible(self.cfg.final_collect_sec)
        try:
            per_account = await self.db.per_account_stats(since=self.started_at)
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="E", op="per_account_stats", error=exc)
            per_account = []
        try:
            pipeline = await self.db.pipeline_totals(since=self.started_at)
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="E", op="pipeline_totals", error=exc)
            pipeline = {}
        try:
            final_speed = await self.db.add_speed_snapshot(
                parser_id=self.cfg.parser_id, since=self.started_at
            )
        except Exception as exc:  # noqa: BLE001
            self.errors.add(phase="E", op="final_speed", error=exc)
            final_speed = {}

        summary = {
            "started_at": self.started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "per_account": per_account,
            "pipeline": pipeline,
            "final_speed": final_speed,
        }
        self.phase_results["E"] = summary
        self._dump("phase_e.json", summary)

        # CSV per-account
        if per_account:
            csv_path = self.cfg.run_dir / "per_account.csv"
            keys = list(per_account[0].keys())
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                for row in per_account:
                    w.writerow({k: row.get(k) for k in keys})
        return summary

    async def _sleep_interruptible(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self.stop.is_set():
                return
            await asyncio.sleep(min(1.0, end - time.monotonic()))

    def runtime_summary(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "users": [
                {
                    "project": rt.plan.project_name,
                    "project_id": rt.plan.project_id,
                    "refs": len(rt.plan.refs),
                    "add_task_ids": len(rt.add_task_ids),
                    "remove_task_ids": len(rt.remove_task_ids),
                    "change_ops": len(rt.change_ops),
                    "enqueue_verify": rt.enqueue_verify,
                }
                for rt in self.runtimes
            ],
            "phases": self.phase_results,
        }
