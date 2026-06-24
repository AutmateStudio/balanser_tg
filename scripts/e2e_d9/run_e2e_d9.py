#!/usr/bin/env python3
"""D9 — E2E: Discovery API remove-channels → PG → worker → clump.

Симметрия D12/D8 для parser_remove_channel.

Использование:
  set -a && source scripts/e2e_d9/env.d9 && set +a
  python scripts/e2e_d9/run_e2e_d9.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_E2E_DIR = Path(__file__).resolve().parent
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))

from e2e_lib import (  # noqa: E402
    DiscoveryClient,
    channel_in_list,
    env,
    env_bool,
    env_float,
    validate_d9_enqueue_response,
    verify_pg_task,
)


def main() -> int:
    base = env("DISCOVERY_BASE_URL")
    api_key = env("DISCOVERY_API_KEY")
    channel_ref = env("E2E_CHANNEL_REF")
    parser_id = env("PARSER_ID")
    poll_interval = env_float("E2E_POLL_INTERVAL_SECONDS", 2.0)
    poll_timeout = env_float("E2E_POLL_TIMEOUT_SECONDS", 180.0)
    verify_b9 = env_bool("E2E_VERIFY_TASK_ATTEMPTS", False)
    skip_pg = env_bool("E2E_SKIP_VERIFY_PG")

    if not base or not api_key or not channel_ref:
        print(
            "Задайте DISCOVERY_BASE_URL, DISCOVERY_API_KEY, E2E_CHANNEL_REF "
            "(см. scripts/e2e_d9/env.d9.example)",
            file=sys.stderr,
        )
        return 1

    client = DiscoveryClient(base, api_key)
    print("=== D9 E2E run (remove-channels) ===")
    print(f"  Discovery: {base}")

    if not parser_id:
        print("PARSER_ID обязателен для D9 (канал должен быть в clump)", file=sys.stderr)
        return 1

    channels_before = client.list_channels(parser_id)
    if not channel_in_list(channel_ref, channels_before):
        print(
            f"FAIL: канал {channel_ref} должен быть в clump до remove "
            f"(channel_list={channels_before.get('channel_list')})",
            file=sys.stderr,
        )
        return 1

    print(f"  POST remove-channels async (D9) channel={channel_ref}")
    remove_resp = client.remove_channels_async(parser_id, [channel_ref])

    d9_errors = validate_d9_enqueue_response(remove_resp)
    print(
        f"  ответ D9: async_mode={remove_resp.get('async_mode')} "
        f"action_id={remove_resp.get('action_id')} task_ids={remove_resp.get('task_ids')}"
    )
    if d9_errors:
        for err in d9_errors:
            print(f"FAIL D9: {err}", file=sys.stderr)
        return 1

    task_id = int(remove_resp["task_ids"][0])
    deadline = time.monotonic() + poll_timeout
    last_status = ""
    api_attempt_count: int | None = None

    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        status = str(task.get("status", ""))
        last_status = status
        api_attempt_count = int(task.get("attempt_count") or 0)
        err = task.get("last_error")
        print(
            f"  poll D10 task_id={task_id} status={status} "
            f"attempt_count={api_attempt_count} error={err or '-'}"
        )
        if status == "done":
            break
        if status in ("failed", "stuck"):
            print(f"FAIL: задача {status}", file=sys.stderr)
            return 1
        time.sleep(poll_interval)
    else:
        print(f"FAIL: timeout {poll_timeout}s, последний status={last_status}", file=sys.stderr)
        return 1

    channels_after = client.list_channels(parser_id)
    if channel_in_list(channel_ref, channels_after):
        print(
            f"FAIL: канал {channel_ref} всё ещё в clump после remove",
            file=sys.stderr,
        )
        return 1

    print(f"  channels OK: {channel_ref} удалён из clump")

    if env("QUEUE_DATABASE_URL") and not skip_pg:
        lines = asyncio.run(
            verify_pg_task(
                task_id,
                channel_ref=channel_ref,
                api_attempt_count=api_attempt_count,
                verify_attempts_table=verify_b9,
                verify_usage=True,
            )
        )
        for line in lines:
            print(f"  {line}")

    print()
    print("=== D9 E2E: УСПЕХ ===")
    print(f"  parser_id={parser_id}")
    print(f"  task_id={task_id}")
    print(f"  channel={channel_ref}")
    print("  Подпишите чеклист: scripts/e2e_d9/checklist.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
