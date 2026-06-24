"""D12 E2E — общая логика preflight/run (D8 API cutover, B9 task_attempts, D10 status)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_float(name: str, default: float) -> float:
    raw = env(name)
    if not raw:
        return default
    return float(raw)


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class DiscoveryClient:
    base_url: str
    api_key: str

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{query}"
        data = None
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"URL error {path}: {exc}") from exc

    def health_ok(self) -> bool:
        url = f"{self.base_url.rstrip('/')}/health"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status == 200

    def probe_api_key(self) -> None:
        self._request("GET", "/discovery-api/parser/list")

    def start_parser(
        self,
        session_name: str,
        channel_list: list[str],
        webhook_url: str,
    ) -> str:
        payload: dict[str, Any] = {
            "session_name": session_name,
            "channel_list": channel_list,
            "webhook_url": webhook_url,
        }
        data = self._request("POST", "/discovery-api/parser/start", payload)
        parser_id = data.get("parser_id")
        if not parser_id:
            raise RuntimeError(f"/parser/start не вернул parser_id: {data}")
        return str(parser_id)

    def add_channels_async(self, parser_id: str, channel_list: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/discovery-api/parser/{parser_id}/add-channels",
            {"channel_list": channel_list},
            query="async=true",
        )

    def get_task(self, task_id: int) -> dict[str, Any]:
        return self._request("GET", f"/discovery-api/parser/queue/tasks/{task_id}")

    def list_channels(self, parser_id: str) -> dict[str, Any]:
        return self._request("GET", f"/discovery-api/parser/{parser_id}/channels")


def channel_in_list(channel_ref: str, channels: dict[str, Any]) -> bool:
    ref = channel_ref.strip().lower()
    channel_list = channels.get("channel_list") or []
    for item in channel_list:
        if str(item).strip().lower() == ref:
            return True
        if ref.lstrip("@") in str(item).lower():
            return True
    allowed = channels.get("allowed_chat_ids") or []
    if allowed and ref.startswith("-"):
        try:
            return int(ref) in allowed
        except ValueError:
            pass
    return False


def validate_d8_enqueue_response(body: dict[str, Any]) -> list[str]:
    """Проверка ответа D8: async + action_id (32 hex) + task_ids."""
    errors: list[str] = []
    if not body.get("async_mode"):
        errors.append("async_mode=false (USE_PG_QUEUE=false на discovery?)")
    action_id = body.get("action_id")
    if not action_id or len(str(action_id)) != 32:
        errors.append(f"action_id некорректен: {action_id!r}")
    task_ids = body.get("task_ids") or []
    if not task_ids:
        errors.append("task_ids пуст (dedup или enqueue не создал задачи)")
    return errors


async def check_pg_queue_basics() -> tuple[bool, list[str]]:
    from app_balance.queue import db

    messages: list[str] = []
    ok = True

    if not env("QUEUE_DATABASE_URL"):
        return False, ["QUEUE_DATABASE_URL не задан"]

    try:
        await db.init_pool()
        if not await db.healthcheck():
            return False, ["PostgreSQL healthcheck() → false"]

        async with db.acquire() as conn:
            task_types = await conn.fetchval("SELECT COUNT(*) FROM task_types")
            parser_add = await conn.fetchval(
                "SELECT COUNT(*) FROM task_types WHERE code = 'parser_add_channel'"
            )
            accounts_active = await conn.fetchval(
                "SELECT COUNT(*) FROM accounts WHERE status = 'active'"
            )
            migrations = await conn.fetchval(
                "SELECT COUNT(*) FROM public._migrations_applied"
            )

        messages.append(f"PostgreSQL OK, migrations={migrations}, task_types={task_types}")

        if int(parser_add or 0) == 0:
            ok = False
            messages.append(
                "task_types.parser_add_channel не найден → docker compose run --rm migrate"
            )
        else:
            messages.append("task_types.parser_add_channel — OK")

        if int(accounts_active or 0) == 0:
            ok = False
            messages.append(
                "Нет active accounts → python scripts/sync_accounts_to_pg.py"
            )
        else:
            messages.append(f"active accounts={accounts_active}")

    except Exception as exc:  # noqa: BLE001
        ok = False
        messages.append(f"PostgreSQL ошибка: {exc}")
    finally:
        await db.close_pool()

    return ok, messages


async def check_b9_schema() -> tuple[bool, list[str]]:
    """B9: миграция A10 + enum attempt_status='running' + таблица task_attempts."""
    from app_balance.queue import db

    messages: list[str] = []
    ok = True

    if not env("QUEUE_DATABASE_URL"):
        return False, ["QUEUE_DATABASE_URL не задан"]

    try:
        await db.init_pool()
        async with db.acquire() as conn:
            a10 = await conn.fetchval(
                """
                SELECT 1 FROM public._migrations_applied
                WHERE name = 'A10_attempt_status_running.sql'
                """
            )
            has_running = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'attempt_status' AND e.enumlabel = 'running'
                )
                """
            )
            ta_exists = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'task_attempts'
                """
            )

        if not a10:
            ok = False
            messages.append(
                "Миграция A10_attempt_status_running.sql не накатана → migrate"
            )
        else:
            messages.append("B9 migration A10_attempt_status_running.sql — OK")

        if not has_running:
            ok = False
            messages.append("attempt_status без значения 'running' (B9)")
        else:
            messages.append("attempt_status 'running' — OK")

        if not ta_exists:
            ok = False
            messages.append("таблица task_attempts не найдена")
        else:
            messages.append("таблица task_attempts — OK")

    except Exception as exc:  # noqa: BLE001
        ok = False
        messages.append(f"B9 schema check ошибка: {exc}")
    finally:
        await db.close_pool()

    return ok, messages


async def verify_pg_task(
    task_id: int,
    *,
    channel_ref: str = "",
    api_attempt_count: int | None = None,
    verify_attempts_table: bool = False,
    verify_usage: bool = True,
) -> list[str]:
    """Сверка task_queue, D5 usage, опционально B9 task_attempts."""
    from app_balance.queue import db

    lines: list[str] = []
    await db.init_pool()
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, attempt_count, postpone_count, last_error,
                       account_id, payload
                FROM task_queue WHERE id = $1
                """,
                task_id,
            )
            if not row:
                raise RuntimeError(f"task_queue id={task_id} не найден")

            lines.append(
                f"PG task_queue: status={row['status']} "
                f"attempt_count={row['attempt_count']} postpone={row['postpone_count']} "
                f"account_id={row['account_id']}"
            )
            if row["last_error"]:
                lines.append(f"PG last_error: {row['last_error']}")
            if str(row["status"]) != "done":
                raise RuntimeError(f"PG status={row['status']}, ожидался done")

            if api_attempt_count is not None:
                pg_ac = int(row["attempt_count"])
                if pg_ac != api_attempt_count:
                    raise RuntimeError(
                        f"attempt_count расхождение: API={api_attempt_count} PG={pg_ac}"
                    )
                lines.append(f"D10/B9: attempt_count API=PG={pg_ac}")

            if verify_usage:
                usage_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM account_resource_usage WHERE task_id = $1",
                    task_id,
                )
                lines.append(f"D5 account_resource_usage rows={usage_count}")
                if int(usage_count or 0) == 0:
                    raise RuntimeError("D5: нет записей account_resource_usage для задачи")

            if verify_attempts_table:
                attempts = await conn.fetch(
                    """
                    SELECT id, attempt_number, status, error_code, finished_at
                    FROM task_attempts
                    WHERE task_id = $1
                    ORDER BY attempt_number
                    """,
                    task_id,
                )
                lines.append(f"B9 task_attempts rows={len(attempts)}")
                if not attempts:
                    raise RuntimeError(
                        "B9: нет строк в task_attempts "
                        "(воркер ещё не пишет историю — отключите E2E_VERIFY_TASK_ATTEMPTS)"
                    )
                for att in attempts:
                    lines.append(
                        f"  attempt #{att['attempt_number']} status={att['status']} "
                        f"error_code={att['error_code'] or '-'}"
                    )
                success = [a for a in attempts if str(a["status"]) == "success"]
                if not success:
                    raise RuntimeError("B9: нет success-попытки в task_attempts")

            if channel_ref:
                cnt = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM source_channels sc
                    WHERE sc.external_url ILIKE '%' || $1 || '%'
                       OR sc.name ILIKE '%' || $1 || '%'
                    """,
                    channel_ref.lstrip("@"),
                )
                if int(cnt or 0) > 0:
                    lines.append(f"D7 source_channels: найдено по ref ({cnt})")
    finally:
        await db.close_pool()

    return lines


def check_discovery_api() -> tuple[bool, str]:
    base = env("DISCOVERY_BASE_URL").rstrip("/")
    if not base:
        return True, "DISCOVERY_BASE_URL не задан — пропуск HTTP"

    api_key = env("DISCOVERY_API_KEY")
    if not api_key:
        return False, "DISCOVERY_API_KEY не задан"

    client = DiscoveryClient(base, api_key)
    try:
        if not client.health_ok():
            return False, f"{base}/health → не 200"
        client.probe_api_key()
    except RuntimeError as exc:
        return False, str(exc)
    except urllib.error.URLError as exc:
        return False, f"Discovery недоступен: {exc}"

    return True, f"Discovery API OK ({base})"


def check_e2e_env() -> tuple[bool, list[str]]:
    messages: list[str] = []
    ok = True

    channel = env("E2E_CHANNEL_REF")
    parser_id = env("PARSER_ID")
    session = env("E2E_SESSION_NAME")

    if not channel:
        ok = False
        messages.append("E2E_CHANNEL_REF не задан")
    else:
        messages.append(f"E2E_CHANNEL_REF={channel}")

    if not parser_id and not session:
        ok = False
        messages.append("PARSER_ID или E2E_SESSION_NAME для /parser/start")
    elif parser_id:
        messages.append(f"PARSER_ID={parser_id}")
    else:
        messages.append(f"будет /parser/start session={session}")

    worker_adapter = env("WORKER_TASK_ADAPTER", "mock")
    if worker_adapter in ("mock", ""):
        messages.append(
            "⚠ WORKER_TASK_ADAPTER=mock — на queue-worker нужен clump для D12"
        )

    if env_bool("E2E_VERIFY_TASK_ATTEMPTS"):
        messages.append("E2E_VERIFY_TASK_ATTEMPTS=true — ожидаются строки в task_attempts (B9)")

    return ok, messages
