"""G4/G7 — env-пороги алертов §26.4 и порогов загрузки G7★."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class AlertConfig:
    enabled: bool = True
    cooldown_seconds: int = 1800
    queue_growth_percent: float = 20.0
    queue_growth_window_seconds: int = 900
    oldest_queued_max_seconds: int = 3600
    high_postpone_min: int = 10
    error_rate_min_percent: float = 50.0
    error_rate_min_attempts: int = 5
    webhook_url: str = ""
    monitor_interval_seconds: float = 120.0
    threshold_enabled: bool = True
    threshold_channel_percent: float = 75.0
    threshold_resource_percent: float = 0.0
    max_channels_per_session: int = 500
    telegram_chat_id: str = ""
    bot_token: str = ""

    @classmethod
    def from_env(cls) -> AlertConfig:
        bot_token = (
            os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
        ).strip()
        return cls(
            enabled=_env_flag("ALERT_ENABLED", True),
            cooldown_seconds=_env_int("ALERT_COOLDOWN_SECONDS", 1800),
            queue_growth_percent=_env_float("ALERT_QUEUE_GROWTH_PERCENT", 20.0),
            queue_growth_window_seconds=_env_int(
                "ALERT_QUEUE_GROWTH_WINDOW_SECONDS", 900
            ),
            oldest_queued_max_seconds=_env_int(
                "ALERT_OLDEST_QUEUED_MAX_SECONDS", 3600
            ),
            high_postpone_min=_env_int("ALERT_HIGH_POSTPONE_MIN", 10),
            error_rate_min_percent=_env_float("ALERT_ERROR_RATE_MIN_PERCENT", 50.0),
            error_rate_min_attempts=_env_int("ALERT_ERROR_RATE_MIN_ATTEMPTS", 5),
            webhook_url=os.getenv("ALERT_WEBHOOK_URL", "").strip(),
            monitor_interval_seconds=_env_float("MONITOR_INTERVAL_SECONDS", 120.0),
            threshold_enabled=_env_flag("THRESHOLD_ALERT_ENABLED", True),
            threshold_channel_percent=_env_float("THRESHOLD_CHANNEL_PERCENT", 75.0),
            threshold_resource_percent=_env_float("THRESHOLD_RESOURCE_PERCENT", 0.0),
            max_channels_per_session=max(1, _env_int("MAX_CHANNELS_PER_SESSION", 500)),
            telegram_chat_id=os.getenv("DEV_ALERT_TELEGRAM_CHAT_ID", "").strip(),
            bot_token=bot_token,
        )


@dataclass(frozen=True, slots=True)
class ErrorDetectorConfig:
    """G6 — пороги детектора повторяющихся ошибок per-op."""

    enabled: bool = True
    window_seconds: int = 3600
    min_count: int = 5
    rph_factor: float = 0.7
    min_rph: int = 2
    repeat_window_seconds: int = 86400
    cooldown_seconds: int = 3600
    telegram_chat_id: str = ""
    bot_token: str = ""

    @classmethod
    def from_env(cls) -> "ErrorDetectorConfig":
        bot_token = (
            os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
        ).strip()
        return cls(
            enabled=_env_flag("ERROR_DETECTOR_ENABLED", True),
            window_seconds=_env_int("ERROR_DETECTOR_WINDOW_SECONDS", 3600),
            min_count=_env_int("ERROR_DETECTOR_MIN_COUNT", 5),
            rph_factor=_env_float("ERROR_DETECTOR_RPH_FACTOR", 0.7),
            min_rph=_env_int("ERROR_DETECTOR_MIN_RPH", 2),
            repeat_window_seconds=_env_int(
                "ERROR_DETECTOR_REPEAT_WINDOW_SECONDS", 86400
            ),
            cooldown_seconds=_env_int("ERROR_DETECTOR_COOLDOWN_SECONDS", 3600),
            telegram_chat_id=os.getenv("DEV_ALERT_TELEGRAM_CHAT_ID", "").strip(),
            bot_token=bot_token,
        )
