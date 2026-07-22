"""Точка входа: 2-часовой E2E loadtest."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from .api import DiscoveryApi
from .cleanup import cleanup_run
from .config import load_config
from .db import Db
from .errors import ErrorSink
from .phases import PhaseRunner
from .report import build_report
from .sampler import MetricsSampler
from .seed import build_seed

log = logging.getLogger("loadtest")


def _setup_logging(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(sh)
    root.addHandler(fh)


async def async_main(argv: list[str] | None = None) -> int:
    cfg = load_config(argv)
    _setup_logging(cfg.run_dir)
    log.info(
        "Start loadtest run_id=%s scale=%s parser_id=%s dry_run=%s out=%s",
        cfg.run_id,
        cfg.scale.label,
        cfg.parser_id,
        cfg.dry_run,
        cfg.run_dir,
    )

    # kill-switch file
    stop_flag = cfg.run_dir / "STOP"
    if stop_flag.exists():
        stop_flag.unlink()

    meta = {
        "run_id": cfg.run_id,
        "scale": cfg.scale.label,
        "parser_id": cfg.parser_id,
        "base_url": cfg.base_url,
        "owner_user_id": cfg.owner_user_id,
        "seed": cfg.seed,
        "durations": {
            "enqueue_check_after_sec": cfg.enqueue_check_after_sec,
            "change_phase_duration_sec": cfg.change_phase_duration_sec,
            "final_collect_sec": cfg.final_collect_sec,
        },
    }
    (cfg.run_dir / "config.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    errors = ErrorSink(cfg.run_dir / "errors.jsonl")
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        log.warning("Stop requested (signal/STOP)")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda *_: _request_stop())

    async def watch_stop_file() -> None:
        while not stop_event.is_set():
            if stop_flag.exists():
                _request_stop()
                return
            await asyncio.sleep(1.0)

    db = Db(cfg.pg_url)
    api = DiscoveryApi(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=cfg.http_timeout_sec,
        retries=cfg.http_retries,
        errors=errors,
    )

    sampler_task: asyncio.Task | None = None
    stop_watch_task: asyncio.Task | None = None
    runner: PhaseRunner | None = None
    seed = None

    try:
        await db.connect()
        stop_watch_task = asyncio.create_task(watch_stop_file())

        # Preflight: parser list
        try:
            parsers = await api.parser_list(phase="preflight")
            ids = [
                p.get("parser_id") or p.get("id")
                for p in parsers
                if isinstance(p, dict)
            ]
            if cfg.parser_id not in ids:
                log.warning(
                    "parser_id=%s не найден в /parser/list (ids=%s) — продолжаем",
                    cfg.parser_id,
                    ids[:10],
                )
        except Exception as exc:  # noqa: BLE001
            errors.add(phase="preflight", op="parser_list", error=exc)

        seed = await build_seed(cfg, db)
        runner = PhaseRunner(cfg, api, db, seed, errors, stop_event)

        sampler = MetricsSampler(cfg, api, errors, stop_event)
        sampler_task = asyncio.create_task(sampler.run())

        # Phase A
        if not stop_event.is_set():
            await runner.phase_a_batch_add()

        # Phase B (includes wait)
        if not stop_event.is_set():
            await runner.phase_b_enqueue_check()

        # Phase C+D
        if not stop_event.is_set():
            await runner.phase_cd_speed_and_changes()

        # Phase E
        if not stop_event.is_set():
            await runner.phase_e_final_collect()
        else:
            # даже при stop — попробуем быстрый сбор
            try:
                await runner.phase_e_final_collect()
            except Exception as exc:  # noqa: BLE001
                errors.add(phase="E", op="final_collect_on_stop", error=exc)

    except Exception as exc:  # noqa: BLE001
        log.exception("Fatal harness error (продолжаем отчёт/cleanup): %s", exc)
        errors.add(phase="harness", op="fatal", error=exc)
    finally:
        stop_event.set()
        if sampler_task:
            sampler_task.cancel()
            try:
                await sampler_task
            except asyncio.CancelledError:
                pass
        if stop_watch_task:
            stop_watch_task.cancel()
            try:
                await stop_watch_task
            except asyncio.CancelledError:
                pass

        # Report
        try:
            if runner is not None:
                rt = runner.runtime_summary()
                (cfg.run_dir / "runtime.json").write_text(
                    json.dumps(rt, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                md_path, json_path = build_report(
                    cfg=cfg,
                    phase_results=runner.phase_results,
                    runtime_summary=rt,
                    errors=errors.all(),
                )
                log.info("Report: %s / %s", md_path, json_path)
        except Exception as exc:  # noqa: BLE001
            log.exception("Report failed: %s", exc)
            errors.add(phase="report", op="build_report", error=exc)

        # Cleanup
        if seed is not None:
            try:
                clean = await cleanup_run(
                    cfg=cfg, db=db, api=api, seed=seed, errors=errors
                )
                (cfg.run_dir / "cleanup.json").write_text(
                    json.dumps(clean, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log.info("Cleanup: %s", clean)
            except Exception as exc:  # noqa: BLE001
                errors.add(phase="cleanup", op="cleanup_run", error=exc)

        await api.aclose()
        await db.close()

    log.info("Done. errors=%s out=%s", errors.count(), cfg.run_dir)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(async_main(argv)))


if __name__ == "__main__":
    main()
