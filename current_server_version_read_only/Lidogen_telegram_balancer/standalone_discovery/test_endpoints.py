#!/usr/bin/env python3
"""
Ручные тестовые вызовы эндпойнтов standalone_discovery (FastAPI).

Скрипт НЕ требует requests — использует стандартный urllib.

Запуск (из каталога standalone_discovery):
  python test_endpoints.py health
  python test_endpoints.py parser_start
  python test_endpoints.py parser_status
  python test_endpoints.py parser_list
  python test_endpoints.py parser_stop
  python test_endpoints.py discover
  python test_endpoints.py discover_groups

Переменные окружения:
  DISCOVERY_BASE_URL         (default: http://127.0.0.1:8000)
  DISCOVERY_API_KEY          (обязательно для всех /discovery-api/* кроме /health)

  # Для парсера:
  TELEGRAM_SESSION_NAME      (например: /app/sessions/my_account)
  TELEGRAM_CHANNEL_LIST      (например: @channel1,@channel2,-100123)
  WEBHOOK_URL                (например: https://example.com/hooks/telegram)
  PARSER_ID                  (если вызываете status/stop/delete для конкретного id)

  # Для discovery:
  DISCOVERY_QUERY            (default: "telegram")
  DISCOVERY_DEPTH            (default: 1)
  DISCOVERY_LIMIT            (default: 20)

Замечания:
  - Эндпойнты discover, discover-groups, add-channel-by-link и parser/start
    принимают `session_name` — путь к Telethon `.session`-файлу на сервере
    (передаётся через TELEGRAM_SESSION_NAME).
  - auth/qr выдаёт `session_string`, который нужно один раз сохранить в файл
    `.session` (например, коротким Telethon-скриптом) — дальше работаем только
    через session_name.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _base_url() -> str:
    return _env("DISCOVERY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _api_key() -> str:
    key = _env("DISCOVERY_API_KEY") or _env("API_KEY")
    if not key:
        raise SystemExit("Не задан DISCOVERY_API_KEY (или API_KEY).")
    return key


def _request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    with_api_key: bool = True,
    timeout: float = 30.0,
) -> Tuple[int, str]:
    url = f"{_base_url()}{path}"
    headers = {"Content-Type": "application/json"}
    if with_api_key:
        headers["X-API-Key"] = _api_key()

    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="replace")
            return status, body
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return int(e.code), body


def health() -> None:
    code, body = _request("GET", "/health", with_api_key=False, timeout=10)
    print(code)
    print(body)


def parser_start() -> None:
    session_name = _env("TELEGRAM_SESSION_NAME")
    channels_raw = _env("TELEGRAM_CHANNEL_LIST")
    webhook_url = _env("WEBHOOK_URL")

    if not session_name:
        raise SystemExit("Не задан TELEGRAM_SESSION_NAME (например /app/sessions/my_account).")
    if not channels_raw:
        raise SystemExit("Не задан TELEGRAM_CHANNEL_LIST (например @ch1,@ch2,-100...).")
    if not webhook_url:
        raise SystemExit("Не задан WEBHOOK_URL (куда слать сообщения).")

    channel_list = [c.strip() for c in channels_raw.split(",") if c.strip()]
    body = {"session_name": session_name, "channel_list": channel_list, "webhook_url": webhook_url}
    code, resp = _request("POST", "/discovery-api/parser/start", json_body=body, timeout=60)
    print(code)
    print(resp)


def parser_status() -> None:
    pid = _env("PARSER_ID")
    if not pid:
        raise SystemExit("Не задан PARSER_ID.")
    code, body = _request("GET", f"/discovery-api/parser/status/{pid}", timeout=20)
    print(code)
    print(body)


def parser_list() -> None:
    code, body = _request("GET", "/discovery-api/parser/list", timeout=20)
    print(code)
    print(body)


def parser_stop() -> None:
    pid = _env("PARSER_ID")
    if not pid:
        raise SystemExit("Не задан PARSER_ID.")
    code, body = _request("POST", f"/discovery-api/parser/stop/{pid}", json_body={}, timeout=20)
    print(code)
    print(body)


def parser_delete() -> None:
    pid = _env("PARSER_ID")
    if not pid:
        raise SystemExit("Не задан PARSER_ID.")
    code, body = _request("DELETE", f"/discovery-api/parser/{pid}", timeout=20)
    print(code)
    print(body)


def discover() -> None:
    query = _env("DISCOVERY_QUERY", "telegram")
    depth = int(_env("DISCOVERY_DEPTH", "2"))
    limit = int(_env("DISCOVERY_LIMIT", "10"))
    session_name = _env("TELEGRAM_SESSION_NAME")
    if not session_name:
        raise SystemExit("Не задан TELEGRAM_SESSION_NAME (например /app/sessions/my_account).")

    body = {
        "session_name": session_name,
        "query": query,
        "first_pass_limit": limit,
        "similarity_depth": depth,
    }
    code, resp = _request("POST", "/discovery-api/discover", json_body=body, timeout=120)
    print(code)
    print(resp)


def discover_groups() -> None:
    word = _env("DISCOVERY_QUERY", "telegram")
    depth = int(_env("DISCOVERY_DEPTH", "2"))
    limit = int(_env("DISCOVERY_LIMIT", "20"))
    session_name = _env("TELEGRAM_SESSION_NAME")
    if not session_name:
        raise SystemExit("Не задан TELEGRAM_SESSION_NAME (например /app/sessions/my_account).")

    body = {
        "session_name": session_name,
        "word": word,
        "limit": limit,
        "depth": depth,
    }
    code, resp = _request("POST", "/discovery-api/discover-groups", json_body=body, timeout=120)
    print(code)
    print(resp)


COMMANDS = {
    "health": health,
    "parser_start": parser_start,
    "parser_status": parser_status,
    "parser_list": parser_list,
    "parser_stop": parser_stop,
    "parser_delete": parser_delete,
    "discover": discover,
    "discover_groups": discover_groups,
}


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help"}:
        print("Команды:")
        for k in sorted(COMMANDS.keys()):
            print(f"  - {k}")
        print("\nПример:")
        print("  set DISCOVERY_API_KEY=... && python test_endpoints.py parser_list")
        raise SystemExit(0)

    cmd = argv[1].strip()
    fn = COMMANDS.get(cmd)
    if not fn:
        raise SystemExit(f"Неизвестная команда: {cmd}")
    fn()


if __name__ == "__main__":
    main(sys.argv)

