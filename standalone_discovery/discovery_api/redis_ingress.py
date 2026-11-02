"""Redis Streams ingress for Telegram messages."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

log = logging.getLogger(__name__)
STREAM_VERSION = "telegram-ingress.v1"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def get_stream() -> str:
    return _env("TELEGRAM_INGRESS_STREAM", "telegram_ingress")


def enabled() -> bool:
    return _env("TELEGRAM_INGRESS_MODE", "direct").lower() == "redis" and bool(
        _env("TELEGRAM_INGRESS_REDIS_URL")
    )


def event_id(payload: dict[str, Any]) -> str:
    message = payload.get("telegram_message") or {}
    return f"telegram:{message.get('chat_id')}:{message.get('id')}"


class Publisher:
    def __init__(self) -> None:
        if not enabled():
            raise RuntimeError("Telegram Redis ingress is not configured")
        self.redis: Redis | None = None
        self.lock = asyncio.Lock()

    async def client(self) -> Redis:
        async with self.lock:
            if self.redis is None:
                self.redis = Redis.from_url(
                    _env("TELEGRAM_INGRESS_REDIS_URL"),
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=5,
                    health_check_interval=30,
                )
            return self.redis

    async def publish(self, payload: dict[str, Any]) -> str:
        eid = event_id(payload)
        envelope = {
            "event_id": eid,
            "schema_version": STREAM_VERSION,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        client = await self.client()
        return str(
            await client.xadd(
                get_stream(),
                {"event_id": eid, "payload": json.dumps(envelope, ensure_ascii=False)},
                maxlen=int(_env("TELEGRAM_INGRESS_MAXLEN", "1000000")),
                approximate=True,
            )
        )

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None


_publisher: Publisher | None = None


async def publish(payload: dict[str, Any]) -> str:
    global _publisher
    if _publisher is None:
        _publisher = Publisher()
    attempts = max(1, int(_env("TELEGRAM_INGRESS_PUBLISH_ATTEMPTS", "3")))
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(_publisher.publish(payload), timeout=5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25 * (2**attempt))
    raise RuntimeError(f"Redis ingress publish failed: {last_error}") from last_error


async def close() -> None:
    global _publisher
    if _publisher is not None:
        await _publisher.close()
        _publisher = None
