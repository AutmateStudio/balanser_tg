"""Локальные смоук-тесты harness без prod DB/API."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from loadtest.prod_e2e.config import Config, Scale, parse_scale
from loadtest.prod_e2e.db import normalize_ref
from loadtest.prod_e2e.errors import ErrorSink
from loadtest.prod_e2e.report import build_report


class TestLoadtestHelpers(unittest.TestCase):
    def test_parse_scale_20x100(self) -> None:
        s = parse_scale("20x100")
        self.assertEqual(s.users, 20)
        self.assertEqual(s.channels_per_user, 100)
        self.assertEqual(s.shared_count, 20)
        self.assertEqual(s.unique_count, 80)

    def test_parse_scale_2x5(self) -> None:
        s = parse_scale("2x5")
        self.assertEqual(s.shared_count, 1)
        self.assertEqual(s.unique_count, 4)

    def test_normalize_ref(self) -> None:
        self.assertEqual(normalize_ref("https://t.me/durov/123"), "durov")
        self.assertEqual(normalize_ref("@channel"), "channel")
        self.assertEqual(normalize_ref("t.me/foo?single"), "foo")

    def test_report_builds_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            cfg = Config(
                base_url="https://example.test",
                api_key="k",
                pg_url="postgresql://x",
                parser_id="pid",
                owner_user_id=1,
                scale=Scale(2, 5),
                run_id="TEST_RUN",
                out_dir=out,
            )
            cfg.run_dir.mkdir(parents=True, exist_ok=True)
            sink = ErrorSink(cfg.run_dir / "errors.jsonl")
            sink.add(phase="A", op="add-channels", error="boom", user_key="u0")
            md, js = build_report(
                cfg=cfg,
                phase_results={
                    "A": {"total_task_ids": 0},
                    "B": {
                        "global": {
                            "expected": 10,
                            "found_tasks": 8,
                            "present_refs": 8,
                            "missing_count": 2,
                            "completeness": 0.8,
                            "status_counts": {"queued": 8},
                            "missing_refs": ["a", "b"],
                        }
                    },
                    "CD": {"change_ops_count": 3, "remove_latency": {}, "add_latency": {}},
                    "E": {
                        "per_account": [
                            {
                                "account_id": 1,
                                "session_name": "acc1",
                                "messages_total": 5,
                                "l2_total": 2,
                                "l2_leads": 1,
                                "l2_filtered": 1,
                                "time_to_first_lead_sec": 12.5,
                            }
                        ],
                        "pipeline": {"messages_ingested": 5, "l2_leads": 1},
                    },
                },
                runtime_summary={"started_at": datetime.now(timezone.utc).isoformat()},
                errors=sink.all(),
            )
            self.assertTrue(md.exists())
            text = md.read_text(encoding="utf-8")
            self.assertIn("Полнота enqueue", text)
            self.assertIn("Ошибки / сбои", text)
            self.assertIn("acc1", text)
            self.assertTrue(js.exists())


if __name__ == "__main__":
    unittest.main()
