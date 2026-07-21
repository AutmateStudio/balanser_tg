"""Конфигурация 2-часового E2E нагрузочного теста (prod)."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
OUT_ROOT = PACKAGE_DIR / "out"
PROJECT_PREFIX = "LOADTEST-"
CREATED_BY_ADD = "discovery_api:add-channels"
CREATED_BY_REMOVE = "discovery_api:remove-channels"


@dataclass
class Scale:
    users: int
    channels_per_user: int
    shared_ratio: float = 0.20  # 20% общих, 80% уникальных

    @property
    def shared_count(self) -> int:
        return max(1, int(round(self.channels_per_user * self.shared_ratio)))

    @property
    def unique_count(self) -> int:
        return self.channels_per_user - self.shared_count

    @property
    def label(self) -> str:
        return f"{self.users}x{self.channels_per_user}"


@dataclass
class Config:
    base_url: str
    api_key: str
    pg_url: str
    parser_id: str
    owner_user_id: int
    scale: Scale
    seed: int = 42
    chunk_size: int = 25
    enqueue_check_after_sec: int = 600  # t10
    change_phase_duration_sec: int = 6000  # t10..110 (~100 мин)
    final_collect_sec: int = 600  # t110..120
    sampler_interval_sec: int = 30
    stats_interval_sec: int = 60
    change_interval_sec: float = 45.0
    change_remove_fraction: float = 0.15
    change_disable_fraction: float = 0.10
    change_add_extra: int = 3
    http_timeout_sec: float = 60.0
    http_retries: int = 3
    pickable_zero_warn_sec: int = 180
    run_id: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out_dir: Path = field(default_factory=lambda: OUT_ROOT)
    dry_run: bool = False
    skip_cleanup: bool = False
    platform_id: int = 2  # Telegram

    @property
    def run_dir(self) -> Path:
        return self.out_dir / self.run_id

    @property
    def total_duration_sec(self) -> int:
        return (
            self.enqueue_check_after_sec
            + self.change_phase_duration_sec
            + self.final_collect_sec
        )


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise SystemExit(f"Нужна переменная окружения {name}")
    return val


def parse_scale(raw: str) -> Scale:
    """Формат: '20x100' или '2x5'."""
    parts = raw.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise SystemExit(f"Неверный --scale '{raw}', ожидается NxM (например 20x100)")
    return Scale(users=int(parts[0]), channels_per_user=int(parts[1]))


def load_config(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        description="2-часовой E2E нагрузочный тест Lidogen (prod)"
    )
    p.add_argument(
        "--scale",
        default=os.environ.get("LOADTEST_SCALE", "20x100"),
        help="NxM пользователей×каналов (default 20x100; rehearsal: 2x5)",
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("LOADTEST_SEED", "42")))
    p.add_argument(
        "--parser-id",
        default=os.environ.get("LOADTEST_PARSER_ID"),
        help="Существующий running clump/parser_id на prod",
    )
    p.add_argument(
        "--owner-user-id",
        type=int,
        default=int(os.environ.get("LOADTEST_OWNER_USER_ID", "0") or "0"),
        help="users.id владельца тестовых monitoring_projects",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get(
            "LOADTEST_BASE_URL",
            "https://lidogen-balancer-tg-prod.web.oboyma.ai",
        ),
    )
    p.add_argument(
        "--enqueue-check-after",
        type=int,
        default=int(os.environ.get("LOADTEST_ENQUEUE_CHECK_AFTER", "600")),
    )
    p.add_argument(
        "--change-duration",
        type=int,
        default=int(os.environ.get("LOADTEST_CHANGE_DURATION", "6000")),
    )
    p.add_argument(
        "--final-collect",
        type=int,
        default=int(os.environ.get("LOADTEST_FINAL_COLLECT", "600")),
    )
    p.add_argument("--chunk-size", type=int, default=25)
    p.add_argument("--dry-run", action="store_true", help="Только seed+план, без API-мутаций")
    p.add_argument("--skip-cleanup", action="store_true")
    p.add_argument(
        "--out-dir",
        default=str(OUT_ROOT),
        help="Каталог отчётов",
    )
    args = p.parse_args(argv)

    api_key = _env("API_KEY", os.environ.get("LOADTEST_API_KEY"))
    pg_url = _env(
        "QUEUE_DATABASE_URL",
        os.environ.get("LOADTEST_PGURL") or os.environ.get("PGURL"),
    )
    if not args.parser_id:
        raise SystemExit("Нужен --parser-id или LOADTEST_PARSER_ID (running clump)")
    if args.owner_user_id <= 0:
        raise SystemExit("Нужен --owner-user-id или LOADTEST_OWNER_USER_ID > 0")

    scale = parse_scale(args.scale)
    cfg = Config(
        base_url=args.base_url.rstrip("/"),
        api_key=api_key,
        pg_url=pg_url,
        parser_id=args.parser_id,
        owner_user_id=args.owner_user_id,
        scale=scale,
        seed=args.seed,
        chunk_size=args.chunk_size,
        enqueue_check_after_sec=args.enqueue_check_after,
        change_phase_duration_sec=args.change_duration,
        final_collect_sec=args.final_collect,
        dry_run=args.dry_run,
        skip_cleanup=args.skip_cleanup,
        out_dir=Path(args.out_dir),
    )
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    return cfg
