"""HTTP-моки для POST /discovery-api/discover-leads/direct."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app_balance.queue.account_lease import AccountLease, NoAccountAvailableError
from app_balance.queue.accounts import Account
from app_balance.queue.per_op_reading import TaskType
from discovery_api.lead_intent.pipeline import LeadCandidate, LeadDiscoveryResult
from discovery_api.lead_intent.router import router


def _task_type() -> TaskType:
    return TaskType(
        id=1,
        code="telegram_discover_leads",
        name="leads",
        description=None,
        is_enabled=True,
        default_priority=75,
        min_available_resource_percent=20,
        requires_specific_account=False,
        uses_two_accounts=False,
        max_attempts=3,
        retry_delay_seconds=60,
        retry_backoff_multiplier=Decimal("2"),
        max_retry_delay_seconds=1800,
        target_queue_size=None,
        max_postpone_count=100,
        task_timeout_seconds=600,
        created_at=None,
        updated_at=None,
        ops=(),
    )


def _lease() -> AccountLease:
    acc = Account(
        id=9,
        session_name="best_sess",
        status="active",
        is_enabled=True,
        current_task_id=100,
        cooldown_until=None,
        last_used_at=None,
    )
    return AccountLease(
        task_id=100,
        account=acc,
        session_name="best_sess",
        availability_percent=91.5,
        task_type=_task_type(),
    )


@pytest.fixture
def app_client():
    application = FastAPI()
    application.include_router(router)
    return TestClient(application)


def test_discover_leads_direct_ok(app_client: TestClient):
    lease = _lease()
    result = LeadDiscoveryResult(
        query="дизайн",
        seeds=["ищу дизайн"],
        candidates=[
            LeadCandidate(
                peer_id=-1001,
                title="Jobs",
                lead_score=70,
                lead_probability=0.7,
            )
        ],
    )

    @asynccontextmanager
    async def fake_lease(*_a, **_k):
        yield lease

    with (
        patch(
            "discovery_api.lead_intent.router.acquire_best_account_lease",
            new=fake_lease,
        ),
        patch(
            "discovery_api.lead_intent.router.get_or_create_client",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "discovery_api.lead_intent.router.run_lead_intent_on_client",
            new=AsyncMock(return_value=result),
        ),
    ):
        resp = app_client.post(
            "/discovery-api/discover-leads/direct",
            json={"query": "дизайн", "max_seeds": 5, "graph_depth": 0},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["leased_session_name"] == "best_sess"
    assert body["lease_task_id"] == 100
    assert body["lease_availability_percent"] == 91.5
    assert body["total"] == 1
    assert body["async_mode"] is False


def test_discover_leads_direct_503(app_client: TestClient):
    @asynccontextmanager
    async def fail_lease(*_a, **_k):
        raise NoAccountAvailableError("нет свободного аккаунта")
        yield  # pragma: no cover

    with patch(
        "discovery_api.lead_intent.router.acquire_best_account_lease",
        new=fail_lease,
    ):
        resp = app_client.post(
            "/discovery-api/discover-leads/direct",
            json={"query": "дизайн"},
        )

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "no_account_available"
