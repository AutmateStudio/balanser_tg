#!/usr/bin/env python3
"""Проверка эндпойнтов Discovery API с локальной машины.

Безопасный режим (по умолчанию): только чтение и «холостые» запросы
(ожидаемые 404/422 без изменения prod-данных).

Примеры:
  set DISCOVERY_BASE_URL=https://lidogen-balancer-tg-prod.web.oboyma.ai
  set DISCOVERY_API_KEY=your-key
  python scripts/check_discovery_api_endpoints.py

  python scripts/check_discovery_api_endpoints.py \\
    --base-url http://127.0.0.1:8100 \\
    --api-key "$API_KEY" \\
    --connect-timeout 10 --read-timeout 30

  # Telethon/QR (--include-side-effects) используют --telethon-timeout (по умолчанию 90 с)

  # POST с минимальным телом (QR-сессия, валидация Telethon) — только осознанно:
  python scripts/check_discovery_api_endpoints.py --include-side-effects
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class RequestTimeouts:
    """Connect + read таймауты для urllib.

    Кортеж (connect, read) поддерживается только с Python 3.11+.
    На 3.10 передаётся один float = max(connect, read).
    """

    connect: float
    read: float

    def for_urllib(self) -> float | tuple[float, float]:
        if sys.version_info >= (3, 11) and self.connect != self.read:
            return (self.connect, self.read)
        return max(self.connect, self.read)


@dataclass(frozen=True, slots=True)
class TimeoutProfile:
    """Профили таймаутов для разных классов эндпойнтов."""

    health: RequestTimeouts
    default: RequestTimeouts
    telethon: RequestTimeouts

    def for_check(self, check: "EndpointCheck") -> RequestTimeouts:
        if check.timeout is not None:
            return check.timeout
        if check.path_template == "/health" or (
            check.path_fn is None and check.path_template.endswith("/health")
        ):
            return self.health
        if check.category.startswith("discovery-") or check.side_effect:
            return self.telethon
        return self.default


@dataclass
class ProbeResult:
    method: str
    path: str
    category: str
    ok: bool
    status: int | None
    elapsed_ms: float
    detail: str
    skipped: bool = False
    timed_out: bool = False
    timeout_limit_s: float | None = None


@dataclass
class Context:
    parser_id: str | None = None
    session_name: str | None = None
    action_id: str | None = None
    task_id: int | None = None


@dataclass
class EndpointCheck:
    method: str
    path_template: str
    category: str
    auth: bool = True
    query: str = ""
    body: dict[str, Any] | None = None
    accept: frozenset[int] = frozenset({200})
    side_effect: bool = False
    timeout: RequestTimeouts | None = None
    path_fn: Callable[[Context], str] | None = None
    skip_fn: Callable[[Context], str | None] | None = None


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        msg = str(reason or exc).lower()
        return "timed out" in msg or "timeout" in msg
    return False


def _request(
    *,
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    auth: bool,
    query: str,
    body: dict[str, Any] | None,
    timeouts: RequestTimeouts,
) -> tuple[int | None, str, float, bool]:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"

    headers = {"Accept": "application/json", "User-Agent": "check_discovery_api_endpoints/1.0"}
    if auth:
        headers["X-API-Key"] = api_key

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeouts.for_urllib()) as resp:
            elapsed_ms = (time.perf_counter() - started) * 1000
            raw = resp.read(8192).decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200))
            detail = raw[:240].replace("\n", " ") if raw else "(пустое тело)"
            return status, detail, elapsed_ms, False
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raw = exc.read(8192).decode("utf-8", errors="replace")
        detail = raw[:240].replace("\n", " ") if raw else exc.reason
        return int(exc.code), detail, elapsed_ms, False
    except urllib.error.URLError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if _is_timeout_error(exc):
            return (
                None,
                f"таймаут (connect={timeouts.connect}s, read={timeouts.read}s)",
                elapsed_ms,
                True,
            )
        return None, str(exc.reason or exc), elapsed_ms, False
    except (TimeoutError, socket.timeout):
        elapsed_ms = (time.perf_counter() - started) * 1000
        return (
            None,
            f"таймаут (connect={timeouts.connect}s, read={timeouts.read}s)",
            elapsed_ms,
            True,
        )


def _json_get(
    base_url: str,
    api_key: str,
    path: str,
    timeouts: RequestTimeouts,
) -> tuple[int | None, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "check_discovery_api_endpoints/1.0",
        "X-API-Key": api_key,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeouts.for_urllib()) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return None, None
    except OSError:
        return None, None

    if status != 200:
        return status, None
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def discover_context(
    base_url: str,
    api_key: str,
    timeouts: TimeoutProfile,
) -> Context:
    ctx = Context()
    ctx_timeouts = timeouts.default

    _, parsers = _json_get(base_url, api_key, "/discovery-api/parser/list", ctx_timeouts)
    if isinstance(parsers, list) and parsers:
        first = parsers[0]
        if isinstance(first, dict):
            pid = first.get("parser_id")
            if isinstance(pid, str) and pid:
                ctx.parser_id = pid

    _, accounts = _json_get(base_url, api_key, "/discovery-api/parser/accounts/all", ctx_timeouts)
    if isinstance(accounts, dict):
        items = accounts.get("accounts") or accounts.get("items")
        if isinstance(items, list) and items:
            row = items[0]
            if isinstance(row, dict):
                sn = row.get("session_name")
                if isinstance(sn, str) and sn:
                    ctx.session_name = sn
                if ctx.parser_id is None:
                    pid = row.get("parser_id")
                    if isinstance(pid, str) and pid:
                        ctx.parser_id = pid

    _, actions = _json_get(
        base_url, api_key, "/discovery-api/parser/actions?limit=5", ctx_timeouts
    )
    if isinstance(actions, dict):
        items = actions.get("actions") or []
        if isinstance(items, list) and items:
            aid = items[0].get("action_id") if isinstance(items[0], dict) else None
            if isinstance(aid, str) and aid:
                ctx.action_id = aid

    _, metrics = _json_get(
        base_url, api_key, "/discovery-api/parser/queue/metrics", ctx_timeouts
    )
    if isinstance(metrics, dict):
        for key in ("sample_task_id", "latest_task_id"):
            tid = metrics.get(key)
            if isinstance(tid, int):
                ctx.task_id = tid
                break
        tasks = metrics.get("recent_tasks") or metrics.get("tasks")
        if ctx.task_id is None and isinstance(tasks, list) and tasks:
            row = tasks[0]
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                ctx.task_id = int(row["id"])

    return ctx


def _path_parser(ctx: Context) -> str:
    return f"/discovery-api/parser/{ctx.parser_id or '00000000000000000000000000000000'}"


def _path_parser_channels(ctx: Context) -> str:
    pid = ctx.parser_id or "00000000000000000000000000000000"
    return f"/discovery-api/parser/{pid}/channels"


def _path_parser_config(ctx: Context) -> str:
    pid = ctx.parser_id or "00000000000000000000000000000000"
    return f"/discovery-api/parser/{pid}/config"


def _path_parser_status(ctx: Context) -> str:
    pid = ctx.parser_id or "00000000000000000000000000000000"
    return f"/discovery-api/parser/status/{pid}"


def _path_action(ctx: Context) -> str:
    aid = ctx.action_id or "00000000000000000000000000000000"
    return f"/discovery-api/parser/actions/{aid}"


def _path_task(ctx: Context) -> str:
    tid = ctx.task_id if ctx.task_id is not None else 999999999
    return f"/discovery-api/parser/queue/tasks/{tid}"


def _path_account(ctx: Context) -> str:
    sn = ctx.session_name or "__nonexistent_session__"
    return f"/discovery-api/parser/accounts/{urllib.parse.quote(sn, safe='')}"


def _query_account_detail(ctx: Context) -> str:
    sn = ctx.session_name or "__nonexistent_session__"
    return urllib.parse.urlencode({"session_name": sn})


def build_checks(include_side_effects: bool) -> list[EndpointCheck]:
    fake_parser = "00000000000000000000000000000000"
    fake_qr = "00000000000000000000000000000000"

    checks: list[EndpointCheck] = [
        # --- system ---
        EndpointCheck("GET", "/health", "system", auth=False),
        # --- discovery-api (router) ---
        EndpointCheck(
            "GET",
            f"/discovery-api/auth/qr/{fake_qr}/status",
            "discovery",
            accept=frozenset({404}),
        ),
        EndpointCheck(
            "DELETE",
            f"/discovery-api/auth/qr/{fake_qr}",
            "discovery",
            accept=frozenset({200, 404}),
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/discover",
            "discovery-telethon",
            body={},
            accept=frozenset({400, 422, 500}),
            side_effect=True,
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/discover-groups",
            "discovery-telethon",
            body={},
            accept=frozenset({400, 422, 500}),
            side_effect=True,
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/add-channel-by-link",
            "discovery-telethon",
            body={},
            accept=frozenset({400, 422, 500}),
            side_effect=True,
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/add-channel-by-link-session-file",
            "discovery-telethon",
            body={},
            accept=frozenset({400, 422, 500}),
            side_effect=True,
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/bot/send-message",
            "discovery-telethon",
            body={},
            accept=frozenset({400, 422, 500}),
            side_effect=True,
        ),
        EndpointCheck(
            "POST",
            "/discovery-api/auth/qr",
            "discovery-side-effect",
            body={},
            accept=frozenset({200, 400, 422, 500}),
            side_effect=True,
        ),
        # --- parser: read ---
        EndpointCheck("GET", "/discovery-api/parser/list", "parser-read"),
        EndpointCheck("GET", "/discovery-api/parser/settings", "parser-read"),
        EndpointCheck("GET", "/discovery-api/parser/queue/metrics", "parser-read"),
        EndpointCheck("GET", "/discovery-api/parser/accounts/all", "parser-read"),
        EndpointCheck("GET", "/discovery-api/parser/accounts", "parser-read"),
        EndpointCheck("GET", "/discovery-api/parser/actions?limit=10", "parser-read"),
        EndpointCheck(
            "GET",
            "",
            "parser-read",
            path_fn=_path_parser_status,
            accept=frozenset({200, 404}),
        ),
        EndpointCheck(
            "GET",
            "",
            "parser-read",
            path_fn=_path_parser_channels,
            accept=frozenset({200, 404}),
        ),
        EndpointCheck(
            "GET",
            "",
            "parser-read",
            path_fn=_path_parser_config,
            accept=frozenset({200, 404}),
        ),
        EndpointCheck(
            "GET",
            "/discovery-api/parser/account-detail",
            "parser-read",
            query="",  # filled per-run
            accept=frozenset({200, 404, 422}),
        ),
        EndpointCheck(
            "GET",
            "/discovery-api/parser/account-channels",
            "parser-read",
            accept=frozenset({200, 404, 422}),
        ),
        EndpointCheck(
            "GET",
            "",
            "parser-read",
            path_fn=_path_action,
            accept=frozenset({200, 404}),
        ),
        EndpointCheck(
            "GET",
            "",
            "parser-read",
            path_fn=_path_task,
            accept=frozenset({200, 404}),
        ),
        # --- parser: validation probes (no successful mutation) ---
        EndpointCheck(
            "POST",
            "/discovery-api/parser/start",
            "parser-probe",
            body={},
            accept=frozenset({400, 422, 500}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/stop/{fake_parser}",
            "parser-probe",
            accept=frozenset({200, 404, 409}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/{fake_parser}/add-channels",
            "parser-probe",
            body={"channel_list": []},
            accept=frozenset({400, 404, 409, 422}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/{fake_parser}/remove-channels",
            "parser-probe",
            body={"channel_list": []},
            accept=frozenset({400, 404, 409, 422}),
        ),
        EndpointCheck(
            "PATCH",
            f"/discovery-api/parser/{fake_parser}/config",
            "parser-probe",
            body={},
            accept=frozenset({400, 404, 409, 422}),
        ),
        EndpointCheck(
            "PATCH",
            "/discovery-api/parser/account-meta",
            "parser-probe",
            body={},
            accept=frozenset({400, 422, 404}),
        ),
        EndpointCheck(
            "PATCH",
            "",
            "parser-probe",
            path_fn=lambda ctx: f"{_path_account(ctx)}/block",
            body={"blocked": False},
            accept=frozenset({200, 404, 422}),
        ),
        EndpointCheck(
            "PATCH",
            "",
            "parser-probe",
            path_fn=_path_account,
            body={},
            accept=frozenset({200, 404, 422}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/{fake_parser}/enroll-session",
            "parser-probe",
            body={"session_name": "__nonexistent__"},
            accept=frozenset({404, 409, 422}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/{fake_parser}/add-session",
            "parser-probe",
            body={"session_name": "__nonexistent__"},
            accept=frozenset({400, 404, 409, 422}),
        ),
        EndpointCheck(
            "POST",
            f"/discovery-api/parser/{fake_parser}/remove-session",
            "parser-probe",
            body={"session_name": "__nonexistent__"},
            accept=frozenset({400, 404, 409, 422}),
        ),
        EndpointCheck(
            "DELETE",
            "",
            "parser-probe",
            path_fn=_path_parser,
            accept=frozenset({200, 404, 409}),
        ),
        EndpointCheck(
            "DELETE",
            "",
            "parser-probe",
            path_fn=_path_account,
            accept=frozenset({200, 404, 409}),
            side_effect=True,
        ),
    ]

    if not include_side_effects:
        checks = [c for c in checks if not c.side_effect]

    return checks


def run_checks(
    *,
    base_url: str,
    api_key: str,
    timeouts: TimeoutProfile,
    include_side_effects: bool,
) -> tuple[list[ProbeResult], Context]:
    ctx = discover_context(base_url, api_key, timeouts)
    results: list[ProbeResult] = []

    for check in build_checks(include_side_effects):
        skip_reason = check.skip_fn(ctx) if check.skip_fn else None
        if skip_reason:
            results.append(
                ProbeResult(
                    method=check.method,
                    path=check.path_template or "(dynamic)",
                    category=check.category,
                    ok=True,
                    status=None,
                    elapsed_ms=0.0,
                    detail=f"SKIP: {skip_reason}",
                    skipped=True,
                )
            )
            continue

        path = check.path_template
        if check.path_fn is not None:
            path = check.path_fn(ctx)

        query = check.query
        if path == "/discovery-api/parser/account-detail":
            query = _query_account_detail(ctx)
        elif path == "/discovery-api/parser/account-channels":
            query = _query_account_detail(ctx)

        if not api_key and check.auth:
            results.append(
                ProbeResult(
                    method=check.method,
                    path=path,
                    category=check.category,
                    ok=False,
                    status=None,
                    elapsed_ms=0.0,
                    detail="нет DISCOVERY_API_KEY / --api-key",
                )
            )
            continue

        req_timeouts = timeouts.for_check(check)

        status, detail, elapsed_ms, timed_out = _request(
            base_url=base_url,
            api_key=api_key,
            method=check.method,
            path=path,
            auth=check.auth,
            query=query,
            body=check.body,
            timeouts=req_timeouts,
        )

        if timed_out:
            ok = False
        elif status is None:
            ok = False
        else:
            ok = status in check.accept

        results.append(
            ProbeResult(
                method=check.method,
                path=path + (f"?{query}" if query else ""),
                category=check.category,
                ok=ok,
                status=status,
                elapsed_ms=elapsed_ms,
                detail=detail,
                timed_out=timed_out,
                timeout_limit_s=req_timeouts.read,
            )
        )

    return results, ctx


def _print_report(
    results: list[ProbeResult],
    ctx: Context,
    base_url: str,
    *,
    timeouts: TimeoutProfile,
    include_side_effects: bool,
) -> None:
    print(f"Base URL: {base_url}")
    print(
        "Таймауты: "
        f"health={timeouts.health.connect}/{timeouts.health.read}s, "
        f"default={timeouts.default.connect}/{timeouts.default.read}s, "
        f"telethon={timeouts.telethon.connect}/{timeouts.telethon.read}s "
        "(connect/read)"
    )
    print(
        "Контекст: "
        f"parser_id={ctx.parser_id or '—'}, "
        f"session={ctx.session_name or '—'}, "
        f"action_id={ctx.action_id or '—'}, "
        f"task_id={ctx.task_id or '—'}"
    )
    print("-" * 88)

    by_cat: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    for category in sorted(by_cat):
        print(f"\n[{category}]")
        for r in by_cat[category]:
            if r.skipped:
                mark = "SKIP"
            elif r.ok:
                mark = " OK "
            elif r.timed_out:
                mark = " TMO"
            else:
                mark = "FAIL"
            status = "TIMEOUT" if r.timed_out else (str(r.status) if r.status is not None else "—")
            limit = ""
            if r.timeout_limit_s is not None:
                limit = f" | limit read={r.timeout_limit_s:g}s"
            print(f"  [{mark}] {r.method:6} {r.path}")
            print(f"         HTTP {status} | {r.elapsed_ms:.0f} ms{limit} | {r.detail[:120]}")

    total = len(results)
    failed = [r for r in results if not r.ok and not r.skipped]
    timed_out = [r for r in results if r.timed_out]
    skipped = [r for r in results if r.skipped]
    passed = total - len(failed) - len(skipped)

    print("\n" + "-" * 88)
    print(
        f"Итого: {passed} OK, {len(failed)} FAIL"
        + (f" ({len(timed_out)} TIMEOUT)" if timed_out else "")
        + f", {len(skipped)} SKIP (всего {total} проверок)"
    )
    if not include_side_effects:
        print(
            "Подсказка: Telethon/QR/DELETE-аккаунт пропущены. "
            "Добавьте --include-side-effects для полного прогона."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверка эндпойнтов Discovery API с локальной машины",
    )
    parser.add_argument(
        "--base-url",
        default=_env("DISCOVERY_BASE_URL") or "http://127.0.0.1:8100",
        help="Базовый URL без /health (env: DISCOVERY_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=_env("DISCOVERY_API_KEY") or _env("API_KEY"),
        help="X-API-Key (env: DISCOVERY_API_KEY или API_KEY)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=_env_float("DISCOVERY_PROBE_CONNECT_TIMEOUT", 10.0),
        help="Таймаут TCP/connect, сек (env: DISCOVERY_PROBE_CONNECT_TIMEOUT)",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=_env_float(
            "DISCOVERY_PROBE_READ_TIMEOUT",
            _env_float("DISCOVERY_PROBE_TIMEOUT", 30.0),
        ),
        help="Таймаут чтения ответа, сек (env: DISCOVERY_PROBE_READ_TIMEOUT)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Устар.: alias для --read-timeout",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=_env_float("DISCOVERY_PROBE_HEALTH_TIMEOUT", 5.0),
        help="Read-таймаут для GET /health, сек",
    )
    parser.add_argument(
        "--telethon-timeout",
        type=float,
        default=_env_float("DISCOVERY_PROBE_TELETHON_TIMEOUT", 90.0),
        help="Read-таймаут для Telethon/QR (--include-side-effects), сек",
    )
    parser.add_argument(
        "--include-side-effects",
        action="store_true",
        help="Включить QR, Telethon discover и DELETE аккаунта (осторожно на prod)",
    )
    args = parser.parse_args()

    base_url = args.base_url.strip().rstrip("/")
    api_key = (args.api_key or "").strip()

    read_timeout = args.read_timeout
    if args.timeout is not None:
        read_timeout = args.timeout

    connect_timeout = args.connect_timeout
    if connect_timeout > read_timeout:
        print(
            f"Предупреждение: connect-timeout ({connect_timeout}s) > read-timeout "
            f"({read_timeout}s), использую read для connect",
            file=sys.stderr,
        )
        connect_timeout = read_timeout

    timeouts = TimeoutProfile(
        health=RequestTimeouts(connect=min(connect_timeout, args.health_timeout), read=args.health_timeout),
        default=RequestTimeouts(connect=connect_timeout, read=read_timeout),
        telethon=RequestTimeouts(connect=connect_timeout, read=args.telethon_timeout),
    )

    if not base_url:
        print("Задайте --base-url или DISCOVERY_BASE_URL", file=sys.stderr)
        return 2

    if not api_key:
        print(
            "Предупреждение: API-ключ не задан — проверки с auth будут FAIL",
            file=sys.stderr,
        )

    results, ctx = run_checks(
        base_url=base_url,
        api_key=api_key,
        timeouts=timeouts,
        include_side_effects=args.include_side_effects,
    )
    _print_report(
        results,
        ctx,
        base_url,
        timeouts=timeouts,
        include_side_effects=args.include_side_effects,
    )

    failed = [r for r in results if not r.ok and not r.skipped]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
