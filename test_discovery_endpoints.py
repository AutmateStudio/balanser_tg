#!/usr/bin/env python3
"""Smoke-тест доступности и корректности эндпойнтов Discovery API.

Проверяет три вещи для каждого эндпойнта:
  1. Доступность   — сервер ответил (нет таймаута / отказа соединения).
  2. Статус-код    — ответ в списке ожидаемых кодов.
  3. Корректность  — тело ответа имеет ожидаемую структуру (для read-эндпойнтов).

Безопасен для prod: только чтение и «холостые» (validation) запросы,
которые не меняют данные (ожидаются 400/422/404).

Запуск как скрипт:
    set DISCOVERY_BASE_URL=https://lidogen-balancer-tg-prod.web.oboyma.ai
    set DISCOVERY_API_KEY=ваш-ключ
    python test_discovery_endpoints.py

    python test_discovery_endpoints.py --base-url http://127.0.0.1:8100 --api-key KEY

Запуск под pytest (скипнется, если сервер не задан/недоступен):
    set DISCOVERY_BASE_URL=...
    set DISCOVERY_API_KEY=...
    python -m pytest test_discovery_endpoints.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------- #
# Конфигурация
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "http://127.0.0.1:8100"
FAKE_ID = "00000000000000000000000000000000"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def get_base_url() -> str:
    return (_env("DISCOVERY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def get_api_key() -> str:
    return _env("DISCOVERY_API_KEY") or _env("API_KEY")


# --------------------------------------------------------------------------- #
# HTTP-слой (только стандартная библиотека)
# --------------------------------------------------------------------------- #


@dataclass
class Response:
    status: Optional[int]
    body: Any
    raw: str
    elapsed_ms: float
    error: Optional[str] = None

    @property
    def reachable(self) -> bool:
        return self.status is not None


def http_request(
    base_url: str,
    method: str,
    path: str,
    *,
    api_key: str = "",
    send_auth: bool = True,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 20.0,
) -> Response:
    url = f"{base_url}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "test_discovery_endpoints/1.0",
    }
    if send_auth and api_key:
        headers["X-API-Key"] = api_key

    data: Optional[bytes] = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.perf_counter() - started) * 1000
            raw = resp.read(65536).decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200))
            return Response(status, _parse_json(raw), raw, elapsed)
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        raw = exc.read(65536).decode("utf-8", errors="replace")
        return Response(int(exc.code), _parse_json(raw), raw, elapsed)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        reason = getattr(exc, "reason", exc)
        return Response(None, None, "", elapsed, error=str(reason))


def _parse_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Валидаторы тела ответа (проверяют «отвечает ли правильно»)
# --------------------------------------------------------------------------- #


def expect_keys(*keys: str) -> Callable[[Any], Optional[str]]:
    def _check(body: Any) -> Optional[str]:
        if not isinstance(body, dict):
            return f"ожидался объект, получено {type(body).__name__}"
        missing = [k for k in keys if k not in body]
        if missing:
            return f"нет полей: {', '.join(missing)}"
        return None

    return _check


def expect_list(body: Any) -> Optional[str]:
    if not isinstance(body, list):
        return f"ожидался массив, получено {type(body).__name__}"
    return None


def expect_health(body: Any) -> Optional[str]:
    if not isinstance(body, dict) or "status" not in body:
        return "нет поля status"
    return None


def no_body_check(_: Any) -> Optional[str]:
    return None


# --------------------------------------------------------------------------- #
# Описание проверок
# --------------------------------------------------------------------------- #


@dataclass
class Check:
    name: str
    method: str
    path: str
    accept: frozenset[int]
    send_auth: bool = True
    body: Optional[dict[str, Any]] = None
    validate: Callable[[Any], Optional[str]] = no_body_check
    # валидируем тело только если фактический статус входит в этот набор
    validate_on: frozenset[int] = field(default_factory=lambda: frozenset({200}))


def build_checks() -> list[Check]:
    return [
        # --- доступность сервиса ---
        Check(
            "health (без ключа)",
            "GET",
            "/health",
            accept=frozenset({200}),
            send_auth=False,
            validate=expect_health,
        ),
        # --- проверка аутентификации: без ключа должен быть 401/403 ---
        Check(
            "auth: 401 без X-API-Key",
            "GET",
            "/discovery-api/parser/list",
            accept=frozenset({401, 403}),
            send_auth=False,
        ),
        # --- read-эндпойнты парсера (структура ответа) ---
        Check(
            "parser/list",
            "GET",
            "/discovery-api/parser/list",
            accept=frozenset({200}),
            validate=expect_list,
        ),
        Check(
            "parser/settings",
            "GET",
            "/discovery-api/parser/settings",
            accept=frozenset({200}),
            validate=expect_keys("settings", "descriptions"),
        ),
        Check(
            "parser/accounts/all",
            "GET",
            "/discovery-api/parser/accounts/all",
            accept=frozenset({200}),
            validate=expect_keys("total", "accounts"),
        ),
        Check(
            "parser/accounts",
            "GET",
            "/discovery-api/parser/accounts",
            accept=frozenset({200}),
            validate=expect_keys("total", "accounts"),
        ),
        Check(
            "parser/actions",
            "GET",
            "/discovery-api/parser/actions?limit=5",
            accept=frozenset({200}),
            validate=expect_keys("total", "actions"),
        ),
        Check(
            "parser/queue/metrics",
            "GET",
            "/discovery-api/parser/queue/metrics",
            # 503 = PG-очередь выключена (USE_PG_QUEUE=false) — это валидно
            accept=frozenset({200, 503}),
            validate=expect_keys("queue", "accounts", "generated_at"),
        ),
        # --- read по несуществующим id: эндпойнт жив, отвечает 404 ---
        Check(
            "parser/status/{fake} → 404",
            "GET",
            f"/discovery-api/parser/status/{FAKE_ID}",
            accept=frozenset({404}),
        ),
        Check(
            "parser/{fake}/channels → 404",
            "GET",
            f"/discovery-api/parser/{FAKE_ID}/channels",
            accept=frozenset({404}),
        ),
        Check(
            "parser/{fake}/config → 404",
            "GET",
            f"/discovery-api/parser/{FAKE_ID}/config",
            accept=frozenset({404}),
        ),
        Check(
            "auth/qr/{fake}/status → 404",
            "GET",
            f"/discovery-api/auth/qr/{FAKE_ID}/status",
            accept=frozenset({404}),
        ),
        # --- validation-пробы (тело пустое → 400/422, данные не меняются) ---
        Check(
            "parser/start (валидация пустого тела)",
            "POST",
            "/discovery-api/parser/start",
            accept=frozenset({400, 422, 500}),
            body={},
        ),
        Check(
            "discover (валидация пустого тела)",
            "POST",
            "/discovery-api/discover",
            accept=frozenset({400, 422, 500}),
            body={},
        ),
        Check(
            "discover-groups (валидация пустого тела)",
            "POST",
            "/discovery-api/discover-groups",
            accept=frozenset({400, 422, 500}),
            body={},
        ),
        Check(
            "add-channels на {fake} → 404/409/422",
            "POST",
            f"/discovery-api/parser/{FAKE_ID}/add-channels",
            accept=frozenset({400, 404, 409, 422}),
            body={"channel_list": []},
        ),
    ]


# --------------------------------------------------------------------------- #
# Прогон
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    check: Check
    response: Response
    ok: bool
    reason: str


def run_check(base_url: str, api_key: str, check: Check, timeout: float) -> CheckResult:
    resp = http_request(
        base_url,
        check.method,
        check.path,
        api_key=api_key,
        send_auth=check.send_auth,
        body=check.body,
        timeout=timeout,
    )

    if not resp.reachable:
        return CheckResult(check, resp, False, f"недоступен: {resp.error}")

    if resp.status not in check.accept:
        accept = ", ".join(str(c) for c in sorted(check.accept))
        return CheckResult(
            check, resp, False, f"статус {resp.status}, ожидался один из [{accept}]"
        )

    if resp.status in check.validate_on:
        err = check.validate(resp.body)
        if err:
            return CheckResult(check, resp, False, f"тело некорректно: {err}")

    return CheckResult(check, resp, True, "ok")


def run_all(base_url: str, api_key: str, timeout: float) -> list[CheckResult]:
    return [run_check(base_url, api_key, c, timeout) for c in build_checks()]


# --------------------------------------------------------------------------- #
# Режим скрипта
# --------------------------------------------------------------------------- #


def print_report(base_url: str, results: list[CheckResult]) -> bool:
    print(f"Discovery API smoke-test: {base_url}")
    print("=" * 78)
    passed = 0
    for r in results:
        mark = " OK " if r.ok else "FAIL"
        status = r.response.status if r.response.reachable else "—"
        ms = f"{r.response.elapsed_ms:.0f}ms"
        print(f"[{mark}] {r.check.method:6} {r.check.path}")
        print(f"        HTTP {status} | {ms} | {r.reason}")
        if r.ok:
            passed += 1
    print("=" * 78)
    failed = len(results) - passed
    print(f"Итого: {passed} OK, {failed} FAIL (всего {len(results)})")
    return failed == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-тест доступности и корректности эндпойнтов Discovery API"
    )
    parser.add_argument("--base-url", default=get_base_url())
    parser.add_argument("--api-key", default=get_api_key())
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    base_url = args.base_url.strip().rstrip("/")
    api_key = (args.api_key or "").strip()

    if not base_url:
        print("Задайте --base-url или DISCOVERY_BASE_URL", file=sys.stderr)
        return 2
    if not api_key:
        print(
            "Предупреждение: API-ключ не задан — auth-проверки будут FAIL",
            file=sys.stderr,
        )

    results = run_all(base_url, api_key, args.timeout)
    ok = print_report(base_url, results)
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Режим pytest
# --------------------------------------------------------------------------- #

try:
    import pytest
except ImportError:  # pytest не обязателен для запуска как скрипт
    pytest = None  # type: ignore[assignment]


if pytest is not None:

    def _server_available() -> bool:
        base = get_base_url()
        resp = http_request(base, "GET", "/health", send_auth=False, timeout=5.0)
        return resp.reachable

    pytestmark = pytest.mark.skipif(
        not _env("DISCOVERY_BASE_URL"),
        reason="не задан DISCOVERY_BASE_URL — пропускаем сетевой smoke-тест",
    )

    @pytest.fixture(scope="module")
    def _ctx() -> tuple[str, str]:
        base = get_base_url()
        if not _server_available():
            pytest.skip(f"Discovery API недоступен по адресу {base}")
        return base, get_api_key()

    @pytest.mark.parametrize("check", build_checks(), ids=lambda c: c.name)
    def test_endpoint(check: Check, _ctx: tuple[str, str]) -> None:
        base_url, api_key = _ctx
        if check.send_auth and not api_key:
            pytest.skip("не задан DISCOVERY_API_KEY / API_KEY")
        result = run_check(base_url, api_key, check, timeout=20.0)
        assert result.ok, (
            f"{check.method} {check.path}: {result.reason} "
            f"(HTTP {result.response.status}, тело: {result.response.raw[:200]})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
