"""G4 + G6 + G7 — фоновый мониторинг очереди, алерты §26.4, детектор ошибок G6, пороги G7.

Запуск:
    python -m app_balance.queue_monitor
    python -m app_balance.queue_monitor alerts
    python -m app_balance.queue_monitor detector
    python -m app_balance.queue_monitor all
    python -m app_balance.queue_monitor --once
    python -m app_balance.queue_monitor all --interval 120
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from typing import Callable, Literal

from app_balance.queue import db
from app_balance.queue.monitoring.alert_rules import evaluate_alerts
from app_balance.queue.monitoring.config import AlertConfig, ErrorDetectorConfig
from app_balance.queue.monitoring.error_detector import run_detector_tick
from app_balance.queue.monitoring.error_detector_repo import ErrorDetectorRepo
from app_balance.queue.monitoring.metrics_repo import MetricsRepo
from app_balance.queue.monitoring.notify import AlertNotifier
from app_balance.queue.monitoring.queue_growth import QueueGrowthTracker
from app_balance.queue.monitoring.threshold_rules import evaluate_threshold_alerts

logger = logging.getLogger("queue_monitor")

_INTERVAL_ENV = "MONITOR_INTERVAL_SECONDS"
MonitorMode = Literal["alerts", "detector", "all"]


def _resolve_interval(cli_interval: float | None, config: AlertConfig) -> float:
    if cli_interval is not None:
        return cli_interval
    return config.monitor_interval_seconds


async def run_alerts_tick(
    repo: MetricsRepo,
    config: AlertConfig,
    growth: QueueGrowthTracker,
    notifier: AlertNotifier,
) -> int:
    """G4 + G7: метрики → правила → emit."""
    try:
        snapshot, ctx = await repo.fetch_alert_context(config)
    except Exception:  # noqa: BLE001
        logger.exception("monitor: ошибка чтения метрик")
        return 0

    growth.record(snapshot.generated_at, snapshot.queue.total)
    alerts = evaluate_alerts(snapshot, ctx, config, growth)
    if config.threshold_enabled:
        alerts = [*alerts, *evaluate_threshold_alerts(snapshot, config)]

    emitted = 0
    for alert in alerts:
        if await notifier.emit(alert):
            emitted += 1

    if alerts:
        logger.info(
            "monitor: alerts tick — правил=%d отправлено=%d (debounce=%d)",
            len(alerts),
            emitted,
            len(alerts) - emitted,
        )
    else:
        logger.debug("monitor: alerts tick — алертов нет")
    return emitted


async def run_combined_tick(
    mode: MonitorMode,
    *,
    alert_config: AlertConfig,
    detector_config: ErrorDetectorConfig,
    repo: MetricsRepo,
    growth: QueueGrowthTracker,
    notifier: AlertNotifier,
    detector_repo: ErrorDetectorRepo,
) -> int:
    total = 0
    if mode in ("alerts", "all"):
        total += await run_alerts_tick(repo, alert_config, growth, notifier)
    if mode in ("detector", "all"):
        total += await run_detector_tick(
            detector_repo,
            detector_config,
            notifier=notifier,
            alert_config=alert_config,
        )
    return total


async def run_loop(
    interval_seconds: float,
    stop: asyncio.Event,
    *,
    once: bool = False,
    mode: MonitorMode = "alerts",
    alert_config: AlertConfig | None = None,
    detector_config: ErrorDetectorConfig | None = None,
) -> None:
    alerts_cfg = alert_config or AlertConfig.from_env()
    detector_cfg = detector_config or ErrorDetectorConfig.from_env()
    repo = MetricsRepo()
    growth = QueueGrowthTracker(window_seconds=alerts_cfg.queue_growth_window_seconds)
    notifier = AlertNotifier(alerts_cfg)
    detector_repo = ErrorDetectorRepo()

    while not stop.is_set():
        await run_combined_tick(
            mode,
            alert_config=alerts_cfg,
            detector_config=detector_cfg,
            repo=repo,
            growth=growth,
            notifier=notifier,
            detector_repo=detector_repo,
        )
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass


def _install_signal_handlers(stop: Callable[[], None]) -> None:
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, lambda *_: stop())
            except (ValueError, OSError):
                pass


async def serve(
    interval_seconds: float,
    *,
    once: bool = False,
    mode: MonitorMode = "alerts",
) -> None:
    await db.init_pool()
    try:
        stop = asyncio.Event()
        _install_signal_handlers(stop.set)
        alerts_cfg = AlertConfig.from_env()
        detector_cfg = ErrorDetectorConfig.from_env()
        logger.info(
            "monitor: старт (mode=%s interval=%ss once=%s alerts=%s detector=%s)",
            mode,
            interval_seconds,
            once,
            alerts_cfg.enabled,
            detector_cfg.enabled,
        )
        await run_loop(
            interval_seconds,
            stop,
            once=once,
            mode=mode,
            alert_config=alerts_cfg,
            detector_config=detector_cfg,
        )
    finally:
        await db.close_pool()
        logger.info("monitor: остановлен")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue_monitor",
        description="G4 + G6 + G7 — мониторинг очереди, алерты и детектор ошибок",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="alerts",
        choices=["alerts", "detector", "all"],
        help="режим tick: alerts (G4+G7), detector (G6), all",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="один tick и выход (для внешнего cron)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=f"интервал между тиками, сек (дефолт {_INTERVAL_ENV} или 120)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _build_parser().parse_args(argv)
    config = AlertConfig.from_env()
    interval = _resolve_interval(args.interval, config)
    asyncio.run(serve(interval, once=args.once, mode=args.mode))


# Обратная совместимость для test_g4_alert_rules
async def run_tick(
    repo: MetricsRepo,
    config: AlertConfig,
    growth: QueueGrowthTracker,
    notifier: AlertNotifier,
) -> int:
    return await run_alerts_tick(repo, config, growth, notifier)


if __name__ == "__main__":
    main()
