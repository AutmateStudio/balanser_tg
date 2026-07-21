"""HTTP-клиент discovery-api с ретраями."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .errors import ErrorSink

log = logging.getLogger("loadtest.api")


class DiscoveryApi:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        retries: int = 3,
        errors: ErrorSink | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.timeout = timeout
        self.retries = retries
        self.errors = errors
        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers=self.headers,
            timeout=httpx.Timeout(timeout),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        phase: str = "api",
        user_key: str | None = None,
        op: str = "http",
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        last_exc: BaseException | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = await self._client.request(
                    method, path, json=json_body, params=params
                )
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}: {resp.text[:500]}",
                        request=resp.request,
                        response=resp,
                    )
                if resp.status_code >= 400:
                    # 4xx не ретраим (кроме уже обработанных), но не валим тест
                    if self.errors:
                        self.errors.add(
                            phase=phase,
                            op=op,
                            error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                            user_key=user_key,
                            detail={"path": path, "status": resp.status_code},
                        )
                    return {
                        "_error": True,
                        "status": resp.status_code,
                        "body": resp.text[:2000],
                    }
                if resp.status_code == 204 or not resp.content:
                    return None
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — намеренно ловим всё
                last_exc = exc
                log.warning(
                    "%s %s attempt=%s failed: %s", method, path, attempt, exc
                )
                if attempt < self.retries:
                    await asyncio.sleep(min(2 ** attempt, 15))
        if self.errors and last_exc is not None:
            self.errors.add(
                phase=phase,
                op=op,
                error=last_exc,
                user_key=user_key,
                detail={"path": path, "method": method},
            )
        return {"_error": True, "status": 0, "body": str(last_exc)}

    async def add_channels(
        self,
        parser_id: str,
        channel_list: list[str],
        *,
        phase: str,
        user_key: str | None = None,
        force_retry: bool = False,
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/discovery-api/parser/{parser_id}/add-channels",
            phase=phase,
            user_key=user_key,
            op="add-channels",
            json_body={"channel_list": channel_list},
            params={"async": "true", "force_retry": str(force_retry).lower()},
        )
        return data if isinstance(data, dict) else {"_error": True, "body": data}

    async def remove_channels(
        self,
        parser_id: str,
        channel_list: list[str],
        *,
        phase: str,
        user_key: str | None = None,
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/discovery-api/parser/{parser_id}/remove-channels",
            phase=phase,
            user_key=user_key,
            op="remove-channels",
            json_body={"channel_list": channel_list},
            params={"async": "true"},
        )
        return data if isinstance(data, dict) else {"_error": True, "body": data}

    async def queue_metrics(self, *, phase: str = "sampler") -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/discovery-api/parser/queue/metrics",
            phase=phase,
            op="queue-metrics",
        )
        return data if isinstance(data, dict) else {"_error": True, "body": data}

    async def task_status(
        self, task_id: int, *, phase: str = "poll", user_key: str | None = None
    ) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/discovery-api/parser/queue/tasks/{task_id}",
            phase=phase,
            user_key=user_key,
            op="task-status",
        )
        return data if isinstance(data, dict) else {"_error": True, "body": data}

    async def parser_list(self, *, phase: str = "setup") -> list[Any]:
        data = await self._request(
            "GET",
            "/discovery-api/parser/list",
            phase=phase,
            op="parser-list",
        )
        return data if isinstance(data, list) else []
