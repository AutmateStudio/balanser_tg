"""Локальные смоук-тесты throughput harness без prod DB/API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loadtest.throughput_test.queue_swap import (
    BackupTask,
    can_restore_status,
    load_backup,
    pause_marker,
    plan_restore,
    write_backup,
)
from loadtest.throughput_test.report import (
    compute_latency_from_seconds,
    compute_throughput,
    write_reports,
)
from loadtest.throughput_test.state import RunState, StateStore
from loadtest.throughput_test.sync_control import _name_matches


class TestQueueSwap(unittest.TestCase):
    def test_pause_marker(self) -> None:
        self.assertEqual(pause_marker("RUN1"), "throughput-test-paused:RUN1")

    def test_can_restore_status(self) -> None:
        self.assertTrue(can_restore_status("queued"))
        self.assertTrue(can_restore_status("scheduled"))
        self.assertTrue(can_restore_status("retry"))
        self.assertFalse(can_restore_status("done"))
        self.assertFalse(can_restore_status("in_progress"))

    def test_backup_roundtrip(self) -> None:
        tasks = [
            BackupTask(
                id=1,
                status="queued",
                run_after=None,
                dedup_key="parser_add_channel:p:ch1",
                task_type_code="parser_add_channel",
            ),
            BackupTask(
                id=2,
                status="retry",
                run_after="2026-07-21T10:00:00+00:00",
                dedup_key="parser_remove_channel:p:ch2",
                task_type_code="parser_remove_channel",
                priority=50,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue_backup.json"
            write_backup(path, tasks, run_id="T1")
            loaded = load_backup(path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].id, 1)
            self.assertEqual(loaded[1].dedup_key, "parser_remove_channel:p:ch2")
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["run_id"], "T1")
            self.assertEqual(raw["count"], 2)

    def test_plan_restore_no_conflict(self) -> None:
        backup = [
            BackupTask(
                id=10,
                status="queued",
                run_after=None,
                dedup_key="k1",
                task_type_code="parser_add_channel",
            )
        ]
        plan = plan_restore(backup, active_dedup_holders={})
        self.assertEqual(plan["restored"], 1)
        self.assertEqual(plan["skipped_conflict"], 0)
        self.assertEqual(plan["restore_ids"], [10])

    def test_plan_restore_dedup_conflict(self) -> None:
        backup = [
            BackupTask(
                id=10,
                status="queued",
                run_after=None,
                dedup_key="k1",
                task_type_code="parser_add_channel",
            )
        ]
        # другая активная задача с тем же ключом
        plan = plan_restore(backup, active_dedup_holders={"k1": [999]})
        self.assertEqual(plan["restored"], 0)
        self.assertEqual(plan["skipped_conflict"], 1)
        self.assertEqual(plan["conflicts"][0]["id"], 10)

    def test_plan_restore_same_id_holder_ok(self) -> None:
        backup = [
            BackupTask(
                id=10,
                status="queued",
                run_after=None,
                dedup_key="k1",
                task_type_code="parser_add_channel",
            )
        ]
        # holder — сама задача (ещё не cancelled в симуляции) — не конфликт
        plan = plan_restore(backup, active_dedup_holders={"k1": [10]})
        self.assertEqual(plan["restored"], 1)


class TestMetrics(unittest.TestCase):
    def test_throughput_4000_in_8h(self) -> None:
        # если бы все 4000 успели за 8ч
        t = compute_throughput(done_count=4000, window_sec=28800, elapsed_sec=28800)
        self.assertAlmostEqual(t["per_hour"], 500.0, places=1)
        self.assertAlmostEqual(t["per_minute"], 500.0 / 60.0, places=2)

    def test_throughput_partial(self) -> None:
        t = compute_throughput(done_count=160, window_sec=28800, elapsed_sec=3600)
        self.assertAlmostEqual(t["per_hour"], 160.0, places=1)

    def test_throughput_zero_window(self) -> None:
        t = compute_throughput(done_count=10, window_sec=0, elapsed_sec=0)
        self.assertEqual(t["per_hour"], 0.0)

    def test_latency_stats(self) -> None:
        lat = compute_latency_from_seconds([10.0, 20.0, 30.0, 40.0, 100.0])
        self.assertEqual(lat["count"], 5)
        self.assertEqual(lat["min_sec"], 10.0)
        self.assertEqual(lat["max_sec"], 100.0)
        self.assertEqual(lat["p50_sec"], 30.0)

    def test_report_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            add_m = {
                "label": "monitor_add",
                "enqueued": 4000,
                "done": 200,
                "failed": 5,
                "pending": 3795,
                "cancelled": 0,
                "status_counts": {"done": 200, "queued": 3795, "failed": 5},
                "window_sec": 28800,
                "elapsed_sec": 28800,
                "throughput": {"per_hour": 25.0, "per_minute": 0.417, "elapsed_sec": 28800},
                "latency": {"count": 200, "avg_sec": 12.0, "p50_sec": 10.0, "p95_sec": 30.0},
                "hourly_done": [{"hour": "2026-07-21T00:00:00+00:00", "done": 50}],
                "per_account": [
                    {
                        "account_id": 1,
                        "session_name": "s1",
                        "done": 20,
                        "failed": 0,
                        "pending": 0,
                        "avg_latency_sec": 11.0,
                    }
                ],
                "top_errors": [{"error": "FloodWait", "count": 3}],
                "completion_ratio": 0.05,
            }
            md, js = write_reports(
                run_dir=run_dir,
                run_id="TEST",
                parser_id="pid",
                config_snapshot={"add_count": 4000, "wait_recovery_sec": 3600,
                                 "add_window_sec": 28800, "remove_window_sec": 7200},
                add_metrics=add_m,
                remove_metrics=None,
                restore_result={"restored": 10, "skipped_conflict": 1, "conflicts": []},
                sync_restore={"n8n": {"activated": [{"id": "x"}]}, "producers": {"started": []}},
                foreign_tasks={"total": 0, "by_source": []},
                phase_timestamps={"preflight": "2026-07-21T00:00:00+00:00"},
                notes=["test note"],
            )
            self.assertTrue(md.exists())
            text = md.read_text(encoding="utf-8")
            self.assertIn("Throughput test report", text)
            self.assertIn("25.0 задач/час", text)
            self.assertIn("FloodWait", text)
            self.assertIn("add_count: **4000**", text)
            self.assertTrue(js.exists())

    def test_report_postfacto_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            add_m = {
                "label": "postfacto:parser_add_channel",
                "enqueued": 100,
                "done": 50,
                "failed": 0,
                "pending": 50,
                "cancelled": 0,
                "status_counts": {"done": 50, "queued": 50},
                "window_sec": 3600,
                "elapsed_sec": 3600,
                "elapsed_basis": "wall_since→until",
                "throughput": {"per_hour": 50.0, "per_minute": 0.833, "elapsed_sec": 3600},
                "throughput_over_wall": {
                    "per_hour": 50.0,
                    "per_minute": 0.833,
                    "elapsed_sec": 3600,
                },
                "throughput_over_span": {
                    "per_hour": 100.0,
                    "per_minute": 1.667,
                    "elapsed_sec": 1800,
                },
                "latency": {
                    "count": 50,
                    "basis": "created→finished (queue+exec)",
                    "avg_sec": 120.0,
                    "p50_sec": 100.0,
                    "p95_sec": 200.0,
                    "exec_count": 50,
                    "exec_avg_sec": 8.0,
                    "exec_p50_sec": 7.0,
                },
                "hourly_done": [],
                "per_account": [],
                "top_errors": [],
                "completion_ratio": 0.5,
                "scope": "task_ids",
            }
            md, _ = write_reports(
                run_dir=run_dir,
                run_id="PF",
                parser_id="pid",
                config_snapshot={
                    "mode": "postfacto",
                    "since": "2026-07-21T00:00:00+00:00",
                    "until": "2026-07-21T01:00:00+00:00",
                    "wall_hours": 1.0,
                    "scope": "task_ids",
                    "add_count": 100,
                },
                add_metrics=add_m,
                remove_metrics=None,
                restore_result=None,
                sync_restore=None,
                foreign_tasks=None,
                phase_timestamps={},
                notes=[],
            )
            text = md.read_text(encoding="utf-8")
            self.assertIn("mode: **postfacto**", text)
            self.assertIn("add_count: **100**", text)
            self.assertNotIn("add_count: **None**", text)
            self.assertIn("wall_since→until", text)
            self.assertIn("латентность exec", text)
            self.assertIn("first_created→last_done", text)


class TestStateAndSync(unittest.TestCase):
    def test_state_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            st = RunState(run_id="R1", parser_id="p")
            st.mark_phase("sync_off")
            st.complete_phase("preflight")
            st.n8n_deactivated = [{"id": "w1", "name": "tg-parser-sync"}]
            store.save(st)
            loaded = store.load()
            assert loaded is not None
            self.assertEqual(loaded.run_id, "R1")
            self.assertEqual(loaded.phase, "sync_off")
            self.assertIn("preflight", loaded.completed_phases)
            self.assertEqual(loaded.n8n_deactivated[0]["id"], "w1")

    def test_parse_run_after_iso_string(self) -> None:
        from loadtest.throughput_test.queue_swap import parse_run_after
        from datetime import datetime, timezone

        dt = parse_run_after("2026-07-21T20:49:03.982788+00:00")
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertIsNone(parse_run_after(None))
        self.assertIsNone(parse_run_after(""))

    def test_placeholder_and_auto_pick(self) -> None:
        from loadtest.throughput_test.config import (
            is_placeholder_parser_id,
            pick_parser_id_from_list,
        )

        self.assertTrue(is_placeholder_parser_id(None))
        self.assertTrue(is_placeholder_parser_id("<uuid-из-ответа>"))
        self.assertTrue(is_placeholder_parser_id("running-parser-id"))
        self.assertFalse(is_placeholder_parser_id("abc-123-real"))

        picked = pick_parser_id_from_list(
            [
                {"parser_id": "idle-1", "running": False},
                {"parser_id": "run-2", "running": True},
            ]
        )
        self.assertEqual(picked, "run-2")

        only = pick_parser_id_from_list([{"parser_id": "solo", "running": False}])
        self.assertEqual(only, "solo")

    def test_postfacto_window_from_run(self) -> None:
        from loadtest.throughput_test.postfacto import _window_from_run

        ctx = {
            "run_id": "20260721T221400Z",
            "state.json": {
                "parser_id": "abc",
                "phase_timestamps": {
                    "queue_swap": "2026-07-21T22:14:04+00:00",
                    "restore": "2026-07-21T22:16:04+00:00",
                },
            },
            "config.json": {},
        }
        since, until, pid = _window_from_run(ctx)
        self.assertEqual(pid, "abc")
        self.assertEqual(since.isoformat(), "2026-07-21T22:14:04+00:00")
        self.assertEqual(until.isoformat(), "2026-07-21T22:16:04+00:00")

    def test_name_matches_sync_workflows(self) -> None:
        self.assertTrue(_name_matches("tg-parser-sync новый prod API"))
        self.assertTrue(_name_matches("VK-parser-sync"))
        self.assertTrue(_name_matches("добавление по ссылке тг (новый prod API)"))
        self.assertFalse(_name_matches("телеграм поиск"))
        self.assertFalse(_name_matches("отправка уведомления"))


if __name__ == "__main__":
    unittest.main()
