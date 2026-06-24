#!/usr/bin/env python3
"""D12 — E2E: Discovery API (D8) → PG → worker → clump.

Проверки:
  - D8: async_mode, action_id, task_ids из enqueue_parser_add_channels
  - D10: GET /queue/tasks/{id} — status, attempt_count
  - D5: account_resource_usage в PG
  - B9 (опционально): task_attempts при E2E_VERIFY_TASK_ATTEMPTS=true

Использование:
  set -a && source scripts/e2e_d12/env.d12 && set +a
  python scripts/e2e_d12/run_e2e_d12.py

  make e2e-d12-run
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
    validate_d8_enqueue_response,
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
            "(см. scripts/e2e_d12/env.d12.example)",
            file=sys.stderr,
        )
        return 1

    client = DiscoveryClient(base, api_key)
    print("=== D12 E2E run ===")
    print(f"  Discovery: {base}")
    print(f"  B9 task_attempts verify: {verify_b9}")

    if not parser_id:
        session = env("E2E_SESSION_NAME")
        start_channels = [
            c.strip() for c in env("E2E_START_CHANNELS").split(",") if c.strip()
        ]
        webhook = env("E2E_WEBHOOK_URL")
        if not session or not start_channels or not webhook:
            print(
                "PARSER_ID пуст — нужны E2E_SESSION_NAME, E2E_START_CHANNELS, E2E_WEBHOOK_URL",
                file=sys.stderr,
            )
            return 1
        print(f"  Запуск clump session={session} channels={start_channels}")
        parser_id = client.start_parser(session, start_channels, webhook)
        print(f"  parser_id={parser_id}")
        time.sleep(2)

    print(f"  POST add-channels async (D8) channel={channel_ref}")
    add_resp = client.add_channels_async(parser_id, [channel_ref])

    d8_errors = validate_d8_enqueue_response(add_resp)
    print(
        f"  ответ D8: async_mode={add_resp.get('async_mode')} "
        f"action_id={add_resp.get('action_id')} task_ids={add_resp.get('task_ids')}"
    )
    if d8_errors:
        for err in d8_errors:
            print(f"FAIL D8: {err}", file=sys.stderr)
        return 1

    task_id = int(add_resp["task_ids"][0])
    deadline = time.monotonic() + poll_timeout
    last_status = ""
    api_attempt_count: int | None = None

    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        status = str(task.get("status", ""))
        last_status = status
        api_attempt_count = int(task.get("attempt_count") or 0)
        postpone = task.get("postpone_count")
        err = task.get("last_error")
        print(
            f"  poll D10 task_id={task_id} status={status} "
            f"attempt_count={api_attempt_count} postpone={postpone} error={err or '-'}"
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

    channels = client.list_channels(parser_id)
    if not channel_in_list(channel_ref, channels):
        print(
            f"FAIL: канал {channel_ref} не в channel_list: {channels.get('channel_list')}",
            file=sys.stderr,
        )
        return 1

    print(f"  channels OK: {channel_ref} в clump")
    by_session = channels.get("by_session") or {}
    for session, refs in by_session.items():
        if any(channel_ref.lower() in str(r).lower() for r in refs):
            print(f"  назначена сессия: {session}")
            break

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
    print("=== D12 E2E: УСПЕХ ===")
    print(f"  parser_id={parser_id}")
    print(f"  task_id={task_id}")
    print(f"  channel={channel_ref}")
    print("  Подпишите чеклист: scripts/e2e_d12/checklist.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
