from __future__ import annotations

import os

from dotenv import load_dotenv

_env_loaded = False


def _ensure_env_loaded() -> None:
    """
    Загружаем .env только при реальном обращении к конфигу.
    Это убирает побочный эффект при импорте модуля.
    """
    global _env_loaded
    if _env_loaded:
        return
    load_dotenv()
    _env_loaded = True


def get_bot_token() -> str:
    _ensure_env_loaded()
    val = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not val:
        raise RuntimeError("BOT_TOKEN не задан")
    return val


def get_api_id() -> int:
    _ensure_env_loaded()
    val = os.getenv("api_id") or os.getenv("API_ID")
    if not val:
        raise RuntimeError("API_ID (или api_id) не задана")
    return int(val)


def get_api_hash() -> str:
    _ensure_env_loaded()
    val = os.getenv("api_hash") or os.getenv("API_HASH")
    if not val:
        raise RuntimeError("API_HASH (или api_hash) не задана")
    return val


def get_api_key() -> str:
    _ensure_env_loaded()
    return (os.getenv("API_KEY") or "").strip()


def get_webhook_api_key() -> str:
    _ensure_env_loaded()
    return (os.getenv("WEBHOOK_API_KEY") or "").strip()


def get_start_webhook_url() -> str:
    _ensure_env_loaded()
    val = (os.getenv("START_WEBHOOK_URL") or "").strip()
    if not val:
        raise RuntimeError("START_WEBHOOK_URL не задан")
    return val


def _get_int_env(name: str, default: int) -> int:
    _ensure_env_loaded()
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def _get_float_env(name: str, default: float) -> float:
    _ensure_env_loaded()
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return float(raw)


def get_dispatch_workers() -> int:
    return _get_int_env("DISPATCH_WORKERS", 32)


def get_http_pool_limit() -> int:
    return _get_int_env("HTTP_POOL_LIMIT", 200)


def get_http_per_host_limit() -> int:
    return _get_int_env("HTTP_PER_HOST_LIMIT", 64)


def get_http_timeout_seconds() -> float:
    return _get_float_env("HTTP_TIMEOUT_SECONDS", 10.0)


def get_queue_maxsize() -> int:
    return _get_int_env("PARSER_QUEUE_MAXSIZE", 100_000)


def get_entity_resolve_delay_seconds() -> float:
    return _get_float_env("ENTITY_RESOLVE_DELAY_SECONDS", 0.4)


def get_max_channels_per_session() -> int:
    """Максимум каналов (listen-peer) на одну Telethon-сессию внутри clump."""
    return max(1, _get_int_env("MAX_CHANNELS_PER_SESSION", 500))


def _get_bool_env(name: str, default: bool) -> bool:
    _ensure_env_loaded()
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def get_session_health_check_interval() -> float:
    """Интервал (сек) между тиками HealthMonitor по всем сессиям clump."""
    return max(1.0, _get_float_env("SESSION_HEALTH_CHECK_INTERVAL", 30.0))


def get_session_max_reconnects() -> int:
    """Сколько подряд неуспешных reconnect'ов до статуса disconnected."""
    return max(1, _get_int_env("SESSION_MAX_RECONNECTS", 5))


def get_session_reconnect_backoff_base() -> float:
    """База экспоненциального backoff (сек) при reconnect listener'а."""
    return max(0.1, _get_float_env("SESSION_RECONNECT_BACKOFF_BASE", 2.0))


def get_session_reconnect_backoff_max() -> float:
    """Верхняя граница backoff (сек) при reconnect listener'а."""
    return max(1.0, _get_float_env("SESSION_RECONNECT_BACKOFF_MAX", 60.0))


def get_session_flood_migrate_threshold_seconds() -> int:
    """Порог «долгого» FloodWait (сек), при котором запускается миграция каналов."""
    return max(1, _get_int_env("SESSION_FLOOD_MIGRATE_THRESHOLD_SECONDS", 300))


def get_session_resolve_min_interval() -> float:
    """Минимальный интервал (сек) между resolve-RPC на одну сессию."""
    return max(0.0, _get_float_env("SESSION_RESOLVE_MIN_INTERVAL", 0.5))


def get_session_auto_migrate() -> bool:
    """Авто-миграция каналов с упавшей/забаненной сессии на здоровые."""
    return _get_bool_env("SESSION_AUTO_MIGRATE", True)


def get_account_auth_recheck_enabled() -> bool:
    """Account-auth watchdog: периодически пробовать реавторизовать ERROR-сессии."""
    return _get_bool_env("ACCOUNT_AUTH_RECHECK_ENABLED", True)


def get_account_auth_recheck_interval_seconds() -> float:
    """Интервал (сек) между повторными попытками восстановить авторизацию ERROR-сессии.

    Отдельно от SESSION_HEALTH_CHECK_INTERVAL, т.к. реальный RPC
    `is_user_authorized()` дороже обычного health-тика и не должен дёргаться
    так же часто — иначе постоянно неавторизованная сессия будет спамить
    Telegram и логи каждые SESSION_HEALTH_CHECK_INTERVAL секунд.
    """
    return max(30.0, _get_float_env("ACCOUNT_AUTH_RECHECK_INTERVAL_SECONDS", 300.0))


def get_add_channels_per_hour() -> int:
    """Лимит успешных добавлений каналов на сессию в час (0 = без лимита)."""
    return max(0, _get_int_env("ADD_CHANNELS_PER_HOUR", 0))


def get_use_pg_queue() -> bool:
    """D8 — bulk async add-channels через PostgreSQL task_queue вместо SQLite action_queue."""
    return _get_bool_env("USE_PG_QUEUE", False)


def get_inprocess_worker() -> bool:
    """D12 — крутить queue-worker в процессе discovery (общий in-memory clump).

    При true discovery дополнительно к API исполняет задачи parser_add_channel
    через тот же clump (get_clump), что держит /parser/start. Это убирает
    конфликт Telegram-сессий между discovery и отдельным worker'ом и даёт
    discovery API видеть результат add-channels. Требует USE_PG_QUEUE=true.
    """
    return _get_bool_env("DISCOVERY_INPROCESS_WORKER", False)


def get_inprocess_worker_count() -> int:
    """Сколько параллельных in-process worker'ов поднимать (INPROCESS_WORKER_COUNT).

    Каждый worker — отдельная asyncio-задача с независимым claim_next.
    PG-уровень (FOR UPDATE SKIP LOCKED + pick_and_reserve) гарантирует, что
    разные воркеры захватывают разные задачи и разные аккаунты.
    Рекомендуется = числу active-аккаунтов в clump (по умолчанию 4).
    """
    _ensure_env_loaded()
    return max(1, _get_int_env("INPROCESS_WORKER_COUNT", 4))


def get_rebalance_enabled() -> bool:
    return _get_bool_env("REBALANCE_ENABLED", False)


def get_rebalance_idle_start_hour() -> int:
    return max(0, min(23, _get_int_env("REBALANCE_IDLE_START_HOUR", 2)))


def get_rebalance_idle_end_hour() -> int:
    return max(0, min(23, _get_int_env("REBALANCE_IDLE_END_HOUR", 6)))


def get_rebalance_high_watermark_ratio() -> float:
    return max(0.5, min(1.0, _get_float_env("REBALANCE_HIGH_WATERMARK_RATIO", 0.90)))


def get_rebalance_low_watermark_ratio() -> float:
    return max(0.0, min(0.99, _get_float_env("REBALANCE_LOW_WATERMARK_RATIO", 0.60)))


def get_rebalance_min_gap_channels() -> int:
    return max(1, _get_int_env("REBALANCE_MIN_GAP_CHANNELS", 20))


def get_rebalance_max_moves_per_tick() -> int:
    return max(1, _get_int_env("REBALANCE_MAX_MOVES_PER_TICK", 5))


def get_rebalance_cooldown_hours() -> float:
    return max(0.0, _get_float_env("REBALANCE_COOLDOWN_HOURS", 24.0))


def get_lidgen_recent_posts_limit() -> int:
    return max(5, min(200, _get_int_env("LIDGEN_RECENT_POSTS_LIMIT", 30)))


def get_lidgen_members_sample_limit() -> int:
    return max(20, min(500, _get_int_env("LIDGEN_MEMBERS_SAMPLE_LIMIT", 200)))


def get_lidgen_dead_days() -> int:
    return max(30, min(730, _get_int_env("LIDGEN_DEAD_DAYS", 180)))


def get_lidgen_discovery_concurrency() -> int:
    return max(1, min(32, _get_int_env("LIDGEN_DISCOVERY_CONCURRENCY", 8)))


def get_lidgen_min_score_total() -> int:
    """Минимальный score для фильтрации каналов в discovery.

    Приоритет: `LIDGEN_MIN_SCORE_TOTAL`, иначе `DISCOVERY_MIN_CHANNEL_SCORE_RATIO`×100,
    иначе 40.
    """
    _ensure_env_loaded()
    raw = (os.getenv("LIDGEN_MIN_SCORE_TOTAL") or "").strip()
    if raw:
        return max(0, min(100, int(raw)))
    ratio_raw = (os.getenv("DISCOVERY_MIN_CHANNEL_SCORE_RATIO") or "").strip()
    if ratio_raw:
        return max(0, min(100, int(float(ratio_raw) * 100)))
    return 40

