"""Конфигурация теста пропускной способности очереди."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
OUT_ROOT = PACKAGE_DIR / "out"
CREATED_BY_PREFIX = "throughput_test:"
PAUSE_ERROR_PREFIX = "throughput-test-paused:"

PHASES = (
    "preflight",
    "sync_off",
    "queue_swap",
    "wait_recovery",
    "enqueue_add",
    "monitor_add",
    "enqueue_remove",
    "monitor_remove",
    "restore",
    "report",
    "done",
)

# На vps-104 ходим в discovery через loopback: публичный URL даёт hairpin/503.
DEFAULT_BASE_URL = "http://127.0.0.1:8100"

_PLACEHOLDER_IDS = frozenset(
    {
        "running-parser-id",
        "parser-id",
        "changeme",
        "uuid-из-ответа",
        "реальный-uuid-из-list",
        "<running-parser-id>",
        "<uuid-из-ответа>",
        "<реальный-uuid-из-list>",
    }
)


@dataclass
class Config:
    base_url: str
    api_key: str
    pg_url: str
    parser_id: str
    n8n_base_url: str
    n8n_api_key: str | None
    add_count: int = 4000
    wait_recovery_sec: int = 3600
    add_window_sec: int = 28800
    remove_window_sec: int = 7200
    chunk_size: int = 25
    sampler_interval_sec: int = 60
    recovery_log_interval_sec: int = 300
    http_timeout_sec: float = 60.0
    http_retries: int = 3
    candidate_pool_extra: float = 0.10  # запас 10% на skip
    platform_id: int = 2  # Telegram
    skip_n8n: bool = False
    skip_producers: bool = False
    dry_run: bool = False
    restore_only: bool = False
    resume_run_id: str | None = None
    run_id: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    out_dir: Path = field(default_factory=lambda: OUT_ROOT)

    @property
    def run_dir(self) -> Path:
        return self.out_dir / self.run_id

    @property
    def min_candidates(self) -> int:
        return int(self.add_count * (1.0 + self.candidate_pool_extra))


def _load_dotenv_files() -> None:
    """Подтянуть ключи из .env, если переменные ещё не заданы в окружении."""
    candidates = [
        REPO_ROOT / "standalone_discovery" / ".env",
        REPO_ROOT / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, val = raw.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip().strip('"').strip("'").strip("\r")
            if val:
                os.environ[key] = val


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise SystemExit(f"Нужна переменная окружения {name}")
    return val


def is_placeholder_parser_id(value: str | None) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    if "<" in s or ">" in s:
        return True
    if s.lower() in {x.lower() for x in _PLACEHOLDER_IDS}:
        return True
    # шаблон вида uuid-из-...
    if re.search(r"(uuid|parser.?id|changeme|подставьте|реальн)", s, re.I):
        return True
    return False


def pick_parser_id_from_list(items: list[dict[str, Any]]) -> str:
    """Выбрать running clump; если один — его; иначе первый running."""
    parsed: list[tuple[str, bool]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = item.get("parser_id") or item.get("id")
        if not pid:
            continue
        running = bool(item.get("running"))
        parsed.append((str(pid), running))
    if not parsed:
        raise SystemExit(
            "Автоподхват parser_id: /discovery-api/parser/list пуст. "
            "Сначала запустите clump (POST /discovery-api/parser/start)."
        )
    running_ids = [pid for pid, running in parsed if running]
    if len(running_ids) == 1:
        return running_ids[0]
    if len(running_ids) > 1:
        # стабильный выбор: первый running
        return running_ids[0]
    if len(parsed) == 1:
        return parsed[0][0]
    raise SystemExit(
        "Автоподхват parser_id: нет running clump. "
        f"Найдено: {[p for p, _ in parsed]}. "
        "Укажите --parser-id явно или запустите парсер."
    )


def discover_parser_id(*, base_url: str, api_key: str, timeout: float = 30.0) -> str:
    url = f"{base_url.rstrip('/')}/discovery-api/parser/list"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-API-Key": api_key,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(
            f"Автоподхват parser_id: HTTP {exc.code} от {url}: {body}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"Автоподхват parser_id: не удалось GET {url}: {exc}. "
            "Проверьте --base-url http://127.0.0.1:8100 и что discovery-api запущен."
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Автоподхват parser_id: невалидный JSON от {url}"
        ) from exc
    if not isinstance(data, list):
        raise SystemExit(
            f"Автоподхват parser_id: ожидали list, получили {type(data).__name__}"
        )
    return pick_parser_id_from_list(data)


def load_config(argv: list[str] | None = None) -> Config:
    _load_dotenv_files()

    p = argparse.ArgumentParser(
        description=(
            "Тест пропускной способности PG-очереди: "
            "отключение синка -> пауза лимитов -> 4000 add (8ч) -> 4000 remove (2ч) -> restore. "
            "Запуск: python3 -m loadtest.throughput_test"
        )
    )
    p.add_argument(
        "--parser-id",
        default=os.environ.get("THROUGHPUT_PARSER_ID")
        or os.environ.get("LOADTEST_PARSER_ID"),
        help=(
            "Running clump/parser_id. Если не задан — автоподхват "
            "первого running из GET {base}/discovery-api/parser/list"
        ),
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get(
            "THROUGHPUT_BASE_URL",
            os.environ.get("LOADTEST_BASE_URL", DEFAULT_BASE_URL),
        ),
        help=(
            "Базовый URL discovery-api. На vps-104 по умолчанию "
            f"{DEFAULT_BASE_URL} (без hairpin на публичный домен)"
        ),
    )
    p.add_argument(
        "--add-count",
        type=int,
        default=int(os.environ.get("THROUGHPUT_ADD_COUNT", "4000")),
    )
    p.add_argument(
        "--wait-recovery",
        type=int,
        default=int(os.environ.get("THROUGHPUT_WAIT_RECOVERY", "3600")),
        help="Секунды ожидания восстановления RPH-лимитов (default 3600)",
    )
    p.add_argument(
        "--add-window",
        type=int,
        default=int(os.environ.get("THROUGHPUT_ADD_WINDOW", "28800")),
        help="Окно мониторинга add в секундах (default 8ч = 28800)",
    )
    p.add_argument(
        "--remove-window",
        type=int,
        default=int(os.environ.get("THROUGHPUT_REMOVE_WINDOW", "7200")),
        help="Окно мониторинга remove в секундах (default 2ч = 7200)",
    )
    p.add_argument("--chunk-size", type=int, default=25)
    p.add_argument(
        "--sampler-interval",
        type=int,
        default=int(os.environ.get("THROUGHPUT_SAMPLER_INTERVAL", "60")),
    )
    p.add_argument(
        "--n8n-base-url",
        default=os.environ.get("N8N_BASE_URL", "https://mokuegopasan.beget.app"),
    )
    p.add_argument(
        "--skip-n8n",
        action="store_true",
        help="Не трогать n8n (оператор отключит sync вручную)",
    )
    p.add_argument(
        "--skip-producers",
        action="store_true",
        help="Не останавливать docker producer-* контейнеры",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help="Продолжить с сохранённого state.json (run_id)",
    )
    p.add_argument(
        "--restore-only",
        action="store_true",
        help=(
            "Только restore очереди/n8n из state.json предыдущего run "
            "(нужен --resume RUN_ID). Для аварийного восстановления после сбоя."
        ),
    )
    p.add_argument(
        "--out-dir",
        default=str(OUT_ROOT),
        help="Каталог отчётов",
    )
    args = p.parse_args(argv)

    api_key = _env("API_KEY", os.environ.get("THROUGHPUT_API_KEY"))
    pg_url = _env(
        "QUEUE_DATABASE_URL",
        os.environ.get("THROUGHPUT_PGURL")
        or os.environ.get("LOADTEST_PGURL")
        or os.environ.get("PGURL"),
    )
    base_url = str(args.base_url).rstrip("/")

    raw_parser = args.parser_id
    if is_placeholder_parser_id(raw_parser):
        parser_id = discover_parser_id(base_url=base_url, api_key=api_key)
        print(f"Автоподхват parser_id={parser_id} из {base_url}/discovery-api/parser/list")
    else:
        parser_id = str(raw_parser).strip()

    if args.restore_only and not args.resume:
        raise SystemExit("--restore-only требует --resume RUN_ID")

    n8n_api_key = (
        os.environ.get("N8N_API_KEY")
        or _maybe_read_n8n_key_file()
    )
    skip_n8n = bool(args.skip_n8n)
    if not skip_n8n and not n8n_api_key:
        if args.restore_only:
            print(
                "N8N_API_KEY не задан — restore-only с --skip-n8n "
                "(очередь восстановится, n8n не трогаем)"
            )
            skip_n8n = True
        else:
            raise SystemExit(
                "Нужен N8N_API_KEY (или --skip-n8n, если sync отключаете вручную)"
            )

    run_id = args.resume or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg = Config(
        base_url=base_url,
        api_key=api_key,
        pg_url=pg_url,
        parser_id=parser_id,
        n8n_base_url=str(args.n8n_base_url).rstrip("/"),
        n8n_api_key=n8n_api_key,
        add_count=args.add_count,
        wait_recovery_sec=args.wait_recovery,
        add_window_sec=args.add_window,
        remove_window_sec=args.remove_window,
        chunk_size=args.chunk_size,
        sampler_interval_sec=args.sampler_interval,
        skip_n8n=skip_n8n,
        skip_producers=bool(args.skip_producers),
        dry_run=bool(args.dry_run),
        restore_only=bool(args.restore_only),
        resume_run_id=args.resume,
        run_id=run_id,
        out_dir=Path(args.out_dir),
    )
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _maybe_read_n8n_key_file() -> str | None:
    path = Path(os.environ.get("N8N_API_KEY_FILE", "n8n/n8n_api.txt"))
    if not path.is_file():
        path = REPO_ROOT / "n8n" / "n8n_api.txt"
    if path.is_file():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content.splitlines()[0].strip()
    return None
