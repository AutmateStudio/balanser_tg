"""F8 — планировщик продюсеров очереди (cron / docker schedule).

Продюсеры (F2 channel_balancer, F4 collect_extra_data, F5 update_channel) сами
себя не запускают — нужен внешний триггер. Этот модуль даёт единую точку входа
с подкомандами и периодическим запуском, чтобы оформить их как job'ы в
docker-compose (см. сервисы producer-* в docker-compose.yml).

Запуск:
    python -m app_balance.queue_scheduler collect          # бесконечный цикл
    python -m app_balance.queue_scheduler update --once     # один тик и выход
    python -m app_balance.queue_scheduler balancer --interval 120

Интервал по умолчанию берётся из env PRODUCER_INTERVAL_SECONDS (дефолт 60s),
либо из --interval. Режим --once удобен для внешнего cron (один тик на запуск).

channel_balancer (F2) требует in-memory реестр clump'ов: перед запуском
восстанавливаем clump'ы из стора (как делает queue_worker.serve), иначе
продюсер не увидит ни одного аккаунта.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
from typing import Awaitable, Callable

from app_balance.queue import db
from app_balance.queue.producers.base import BaseProducer, ProduceResult
from app_balance.queue.producers.channel_balancer import ChannelBalancerProducer
from app_balance.queue.producers.collect_extra_data import CollectExtraDataProducer
from app_balance.queue.producers.update_channel import UpdateChannelProducer

logger = logging.getLogger("queue_scheduler")

DEFAULT_INTERVAL_SECONDS = 60.0
_INTERVAL_ENV = "PRODUCER_INTERVAL_SECONDS"

# Имя подкоманды -> фабрика продюсера. Для balancer нужен restore clump'ов.
ProducerFactory = Callable[[], BaseProducer]

PRODUCERS: dict[str, ProducerFactory] = {
    "collect": CollectExtraDataProducer,
    "update": UpdateChannelProducer,
    "balancer": ChannelBalancerProducer,
}

# Подкоманды, которым нужен in-memory реестр clump'ов (F2).
_NEEDS_CLUMPS = frozenset({"balancer"})


def _resolve_interval(cli_interval: float | None) -> float:
    if cli_interval is not None:
        return cli_interval
    raw = os.getenv(_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r некорректно — использую дефолт %s",
            _INTERVAL_ENV,
            raw,
            DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    if value <= 0:
        return DEFAULT_INTERVAL_SECONDS
    return value


def _summarize(results: list[ProduceResult]) -> str:
    created = sum(1 for r in results if r.created)
    duplicate = sum(1 for r in results if r.skipped_reason == "duplicate")
    queue_full = sum(1 for r in results if r.skipped_reason == "queue_full")
    return (
        f"создано={created} дубликатов={duplicate} "
        f"queue_full={queue_full} всего={len(results)}"
    )


async def run_tick(producer: BaseProducer) -> list[ProduceResult]:
    """Один тик продюсера с логом сводки. Ошибки логируются, не валят цикл."""
    try:
        results = await producer.produce()
    except Exception:  # noqa: BLE001
        logger.exception("scheduler: ошибка в produce()")
        return []
    logger.info("scheduler: тик завершён — %s", _summarize(results))
    return results


async def run_loop(
    producer: BaseProducer,
    interval_seconds: float,
    stop: asyncio.Event,
    *,
    once: bool = False,
) -> None:
    """Цикл запусков продюсера до сигнала остановки (или один тик при once)."""
    while not stop.is_set():
        await run_tick(producer)
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


async def _restore_clumps() -> None:
    from discovery_api.clump_bootstrap import restore_all_clumps_from_store

    restored = await restore_all_clumps_from_store()
    logger.info("scheduler: восстановлено %d clump(ов)", restored)


async def serve(
    producer_name: str,
    interval_seconds: float,
    *,
    once: bool = False,
    restore_clumps: Callable[[], Awaitable[None]] = _restore_clumps,
) -> None:
    """Полный жизненный цикл: пул + (clumps) + сигналы + цикл + закрытие пула."""
    factory = PRODUCERS[producer_name]
    await db.init_pool()
    try:
        if producer_name in _NEEDS_CLUMPS:
            await restore_clumps()
        producer = factory()
        stop = asyncio.Event()
        _install_signal_handlers(stop.set)
        logger.info(
            "scheduler: старт продюсера %s (interval=%ss once=%s)",
            producer_name,
            interval_seconds,
            once,
        )
        await run_loop(producer, interval_seconds, stop, once=once)
    finally:
        await db.close_pool()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue_scheduler",
        description="F8 — планировщик продюсеров очереди",
    )
    parser.add_argument(
        "producer",
        choices=sorted(PRODUCERS.keys()),
        help="какой продюсер запускать",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="один тик и выход (для внешнего cron)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=f"интервал между тиками, сек (дефолт {_INTERVAL_ENV} или 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _build_parser().parse_args(argv)
    interval = _resolve_interval(args.interval)
    asyncio.run(serve(args.producer, interval, once=args.once))


if __name__ == "__main__":
    main()
