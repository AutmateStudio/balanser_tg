"""D9 E2E — расширение D12 lib для remove-channels."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_D12_DIR = Path(__file__).resolve().parents[1] / "e2e_d12"
if str(_D12_DIR) not in sys.path:
    sys.path.insert(0, str(_D12_DIR))

from e2e_lib import (  # noqa: E402
    DiscoveryClient,
    channel_in_list,
    env,
    env_bool,
    env_float,
    verify_pg_task,
)

__all__ = [
    "DiscoveryClient",
    "channel_in_list",
    "env",
    "env_bool",
    "env_float",
    "verify_pg_task",
    "validate_d9_enqueue_response",
]


def validate_d9_enqueue_response(body: dict[str, Any]) -> list[str]:
    """Проверка ответа D9: async + action_id + task_ids для remove-channels."""
    errors: list[str] = []
    if not body.get("async_mode"):
        errors.append("async_mode=false (USE_PG_QUEUE=false на discovery?)")
    action_id = body.get("action_id")
    if not action_id or len(str(action_id)) != 32:
        errors.append(f"action_id некорректен: {action_id!r}")
    task_ids = body.get("task_ids") or []
    if not task_ids:
        errors.append("task_ids пуст — enqueue_parser_remove_channels не создал задачи")
    return errors


def _extend_discovery_client() -> None:
    if hasattr(DiscoveryClient, "remove_channels_async"):
        return

    def remove_channels_async(
        self: DiscoveryClient,
        parser_id: str,
        channel_list: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/discovery-api/parser/{parser_id}/remove-channels",
            {"channel_list": channel_list},
            query="async=true",
        )

    DiscoveryClient.remove_channels_async = remove_channels_async  # type: ignore[attr-defined]


_extend_discovery_client()
