"""Юнит-тесты ядра lead_intent (без Telethon / без PG)."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discovery_api.lead_intent.graph import extract_graph_seeds_from_messages
from discovery_api.lead_intent.persist import build_lead_intent_metadata, should_persist_lead
from discovery_api.lead_intent.pipeline import LeadCandidate, run_lead_intent_on_client
from discovery_api.lead_intent.scorer import score_messages
from discovery_api.lead_intent.seeds import generate_intent_seeds, is_group_pass_seed


def test_generate_intent_seeds_priority_and_dedup():
    seeds = generate_intent_seeds("дизайн", max_seeds=25, extra_intents=["ищу дизайнера"])
    assert seeds[0] == "ищу дизайнера"
    assert "ищу дизайн" in seeds
    assert "дизайн чат" in seeds or "дизайн вакансия" in seeds
    assert len(seeds) == len({s.lower() for s in seeds})
    assert len(seeds) <= 25


def test_generate_intent_seeds_design_extras():
    seeds = generate_intent_seeds("дизайн", max_seeds=40)
    assert "сверстать" in seeds or "отрисовать" in seeds


def test_generate_intent_seeds_empty_query():
    assert generate_intent_seeds("  ") == []


def test_is_group_pass_seed():
    assert is_group_pass_seed("дизайн чат", "дизайн")
    assert is_group_pass_seed("дизайн вакансия", "дизайн")
    assert not is_group_pass_seed("ищу дизайн", "дизайн")


def test_score_messages_lead_and_spam():
    msgs = [
        {"text": "Ищу дизайнера, бюджет 50к, портфолио в личку"},
        {"text": "Курсы по дизайну и вдохновение, референс дня"},
        {"text": "Смотрите https://www.behance.net/gallery/1", "file_ext": ".pdf"},
    ]
    result = score_messages(msgs, niche="дизайн")
    assert result.lead_score >= 30
    assert "ищу" in result.intent_hits or "бюджет" in result.intent_hits
    assert result.spam_hits
    assert result.tz_files == 1
    assert 0.0 <= result.lead_probability <= 1.0
    assert result.lead_score > 0


def test_score_messages_community_ratio():
    msgs = [{"text": "привет"}]
    result = score_messages(msgs, is_megagroup=True, community_ratio=0.85)
    assert result.is_community is True
    assert result.lead_score == 0


def test_score_client_base_broadcast():
    msgs = [{"text": "Нужен подрядчик, бюджет обсудим"}]
    result = score_messages(msgs, is_broadcast=True)
    assert result.is_client_base is True


def test_extract_graph_seeds():
    class Fwd:
        from_id = type("PeerChannel", (), {"channel_id": 12345})()

    class Msg:
        message = "Смотрите заказ в @cool_jobs и ещё @Cool_Jobs"
        fwd_from = Fwd()
        file = None

    seeds = extract_graph_seeds_from_messages([Msg()], max_seeds=10)
    assert "@cool_jobs" in seeds
    assert "channel:12345" in seeds
    assert len([s for s in seeds if s == "@cool_jobs"]) == 1


def test_should_persist_and_metadata():
    low = LeadCandidate(
        peer_id=1,
        title="x",
        lead_score=10,
        intent_hits=[],
        is_broadcast=True,
    )
    assert should_persist_lead(low, min_score=50) is False

    high = LeadCandidate(
        peer_id=2,
        title="Jobs",
        username="jobs",
        lead_score=70,
        lead_probability=0.7,
        intent_hits=["ищу", "бюджет"],
        is_job_board=True,
        is_broadcast=False,
    )
    assert should_persist_lead(high, min_score=50) is True
    meta = build_lead_intent_metadata(high)
    assert "lead_intent" in meta
    assert meta["lead_intent"]["lead_score"] == 70
    assert meta["lead_intent"]["pipeline"] == "lead_intent_v1"


def test_pipeline_mock_client_upsert_metadata():
    """Интеграция на моках: 1 сид → 1 кандидат → persist metadata.lead_intent."""

    class FakeChannel:
        def __init__(self):
            self.id = 777001
            self.title = "Design Jobs"
            self.username = "design_jobs_test"
            self.broadcast = False
            self.megagroup = True
            self.access_hash = 123
            self.participants_count = 100

    fake_ch = FakeChannel()

    async def fake_search_global(*_a, **_k):
        return [fake_ch], None

    async def fake_search_contacts(*_a, **_k):
        return [], None

    async def fake_score_entity(*_a, **_k):
        return LeadCandidate(
            peer_id=-100777001,
            title="Design Jobs",
            username="design_jobs_test",
            lead_score=72,
            lead_probability=0.72,
            intent_hits=["ищу", "бюджет"],
            is_job_board=True,
            matched_seed="ищу дизайн",
            source="search_global",
            entity=fake_ch,
        )

    class FakeUpsert:
        def __init__(self):
            self.calls = []

        async def upsert_discovered(self, **kwargs):
            self.calls.append(kwargs)

            class R:
                channel_id = 42
                inserted = True

            return R()

    repo = FakeUpsert()
    client = MagicMock()

    async def _run():
        with (
            patch(
                "discovery_api.lead_intent.pipeline.search_global_pages",
                new=fake_search_global,
            ),
            patch(
                "discovery_api.lead_intent.pipeline.search_contacts",
                new=fake_search_contacts,
            ),
            patch(
                "discovery_api.lead_intent.pipeline.peer_id_from_entity",
                side_effect=lambda e: -100777001 if e is fake_ch else None,
            ),
            patch(
                "discovery_api.lead_intent.pipeline._score_entity",
                new=fake_score_entity,
            ),
            patch(
                "discovery_api.lead_intent.persist.get_telegram_platform_id",
                new=AsyncMock(return_value=2),
            ),
        ):
            return await run_lead_intent_on_client(
                client,
                "дизайн",
                max_seeds=3,
                search_pages=1,
                graph_depth=0,
                min_lead_score=50,
                persist=True,
                channels_repo=repo,
                extra_intents=["ищу дизайн"],
            )

    result = asyncio.run(_run())
    assert result.total >= 1
    assert result.candidates[0].lead_score == 72
    assert result.persist is not None
    assert result.persist.inserted == 1
    assert repo.calls
    meta = repo.calls[0]["metadata"]
    assert meta["lead_intent"]["lead_score"] == 72
    assert meta["lead_intent"]["pipeline"] == "lead_intent_v1"
