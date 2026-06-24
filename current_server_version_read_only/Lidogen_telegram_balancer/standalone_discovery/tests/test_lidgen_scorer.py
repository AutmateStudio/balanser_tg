"""Юнит-тесты лидген-скоринга (без Telegram)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.score_channel import lidgen_scorer


def _base_signals(**kwargs: object) -> dict:
    d: dict = {
        "title": "Тестовый канал про маркетинг",
        "username": "testmarketing",
        "about": "Подписывайтесь на новости",
        "participants_count": 5000,
        "online_count": 40,
        "linked_chat_id": None,
        "slowmode_seconds": 0,
        "posts": [],
        "members_sample": {"sampled": 0, "bots": 0, "deleted": 0},
        "meta": {
            "scam": False,
            "fake": False,
            "restricted": False,
            "megagroup": False,
            "broadcast": True,
            "noforwards": False,
            "join_to_send": False,
            "join_request": False,
            "created_at": "2020-01-01T00:00:00+00:00",
        },
        "collector_errors": [],
    }
    d.update(kwargs)
    return d


class LidgenScamFakeTests(unittest.TestCase):
    def test_scam_zeroes_score(self) -> None:
        sig = _base_signals()
        sig["meta"] = {**sig["meta"], "scam": True}
        s = lidgen_scorer.score_channel_for_lidgen(signals=sig, query="маркетинг", depth=0, source="search")
        self.assertEqual(s.score_total, 0)
        self.assertTrue(s.hard_flags.get("scam"))


class LidgenBoundsTests(unittest.TestCase):
    def test_score_in_0_100(self) -> None:
        import time

        now_ts = time.time()
        posts = [
            {
                "date_ts": now_ts - i * 3600,
                "views": 800 + i * 10,
                "forwards": 2,
                "reactions_total": 15,
                "reaction_dominance": 0.4,
                "replies": 0,
            }
            for i in range(15)
        ]
        sig = _base_signals(posts=posts)
        s = lidgen_scorer.score_channel_for_lidgen(signals=sig, query="маркетинг", depth=0, source="search")
        self.assertGreaterEqual(s.score_total, 0)
        self.assertLessEqual(s.score_total, 100)


class LidgenGroupTests(unittest.TestCase):
    def test_group_seed_improves_relevance(self) -> None:
        sig = _base_signals(title="python chat", username="pychat", about="")
        s1 = lidgen_scorer.score_group_for_lidgen(
            signals=sig, query="java", matched_seed=None, depth=0, source="global_search"
        )
        s2 = lidgen_scorer.score_group_for_lidgen(
            signals=sig, query="java", matched_seed="python", depth=0, source="global_search"
        )
        self.assertGreaterEqual(s2.score_total, s1.score_total)


if __name__ == "__main__":
    unittest.main()
