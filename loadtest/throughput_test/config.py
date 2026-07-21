"""Конфигурация теста пропускной способности очереди."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
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


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise SystemExit(f"Нужна переменная окружения {name}")
    return val


def load_config(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        description=(
            "Тест пропускной способности PG-очереди: "
            "отключение синка -> пауза лимитов -> 4000 add (8ч) -> 4000 remove (2ч) -> restore"
        )
    )
    p.add_argument(
        "--parser-id",
        default=os.environ.get("THROUGHPUT_PARSER_ID")
        or os.environ.get("LOADTEST_PARSER_ID"),
        help="Running clump/parser_id на prod",
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
    if not args.parser_id:
        raise SystemExit(
            "Нужен --parser-id или THROUGHPUT_PARSER_ID / LOADTEST_PARSER_ID"
        )
    parser_id = str(args.parser_id).strip()
    if (
        not parser_id
        or "<" in parser_id
        or ">" in parser_id
        or parser_id.lower() in {"running-parser-id", "parser-id", "changeme"}
    ):
        raise SystemExit(
            f"Некорректный parser_id={parser_id!r}. "
            "Подставьте реальный id из: "
            "curl -sS -H \"X-API-Key: $API_KEY\" "
            "http://127.0.0.1:8100/discovery-api/parser/list"
        )
    if args.restore_only and not args.resume:
        raise SystemExit("--restore-only требует --resume RUN_ID")

    n8n_api_key = (
        os.environ.get("N8N_API_KEY")
        or _maybe_read_n8n_key_file()
    )
    if not args.skip_n8n and not n8n_api_key:
        raise SystemExit(
            "Нужен N8N_API_KEY (или --skip-n8n, если sync отключаете вручную)"
        )

    run_id = args.resume or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg = Config(
        base_url=args.base_url.rstrip("/"),
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
        skip_n8n=bool(args.skip_n8n),
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
    if path.is_file():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content.splitlines()[0].strip()
    return None
