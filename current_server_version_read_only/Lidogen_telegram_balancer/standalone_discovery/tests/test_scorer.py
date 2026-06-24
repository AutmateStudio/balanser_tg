import os
import sys
import unittest

# Чтобы тесты могли импортировать `discovery_api` при запуске из корня проекта.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.score_channel import scorer


class TokenizeTests(unittest.TestCase):
    def test_tokenize_empty(self) -> None:
        self.assertEqual(scorer._tokenize(""), set())

    def test_tokenize_min_length(self) -> None:
        # одиночная буква не должна попадать в токены
        self.assertEqual(scorer._tokenize("a"), set())
        self.assertEqual(scorer._tokenize("ab"), {"ab"})


class OverlapTests(unittest.TestCase):
    def test_overlap_no_target(self) -> None:
        self.assertEqual(scorer._overlap_ratio(["query"], set()), 0.0)

    def test_overlap_some_hits(self) -> None:
        # overlap = hits / len(src)
        # src = ["a", "b", "c"], target = {"b"} => hits=1, len(src)=3 => 0.333...
        val = scorer._overlap_ratio(["a", "b", "c"], {"b"})
        self.assertAlmostEqual(val, 1 / 3, places=6)


class ScoreChannelTests(unittest.TestCase):
    def test_score_total_is_in_bounds(self) -> None:
        score = scorer.score_discovered_channel(
            title="Test Group",
            username="testgroup",
            participants_count=1000,
            query="test",
            depth=1,
            source="search",
        )
        self.assertGreaterEqual(score.score_total, 0)
        self.assertLessEqual(score.score_total, 100)

    def test_score_increases_with_members_count(self) -> None:
        low = scorer.score_discovered_channel(
            title="Test Group",
            username="testgroup",
            participants_count=10,
            query="test",
            depth=0,
            source="search",
        )
        high = scorer.score_discovered_channel(
            title="Test Group",
            username="testgroup",
            participants_count=50000,
            query="test",
            depth=0,
            source="search",
        )
        self.assertGreaterEqual(high.score_total, low.score_total)


class ScoreGroupTests(unittest.TestCase):
    def test_group_score_is_in_bounds(self) -> None:
        score = scorer.score_discovered_group(
            title="Test Community",
            username="testcommunity",
            participants_count=10000,
            messages_30d=120,
            query="test",
            matched_seed="test",
            depth=0,
            source="global_search",
        )
        self.assertGreaterEqual(score.score_total, 0)
        self.assertLessEqual(score.score_total, 100)

    def test_group_activity_affects_score(self) -> None:
        low_activity = scorer.score_discovered_group(
            title="Test Community",
            username="testcommunity",
            participants_count=10000,
            messages_30d=0,
            query="test",
            matched_seed="test",
            depth=0,
            source="global_search",
        )
        high_activity = scorer.score_discovered_group(
            title="Test Community",
            username="testcommunity",
            participants_count=10000,
            messages_30d=500,
            query="test",
            matched_seed="test",
            depth=0,
            source="global_search",
        )
        self.assertGreaterEqual(high_activity.score_total, low_activity.score_total)


if __name__ == "__main__":
    unittest.main()

