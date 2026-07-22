"""Точка входа: тест пропускной способности PG-очереди."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

from loadtest.prod_e2e.api import DiscoveryApi
from loadtest.prod_e2e.errors import ErrorSink

from .config import load_config
from .db import ThroughputDb
from .phases import PhaseOrchestrator, ensure_restore_on_exit
from .state import RunState, StateStore

log = logging.getLogger("throughput")


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

    store = StateStore(cfg.run_dir / "state.json")
    existing = store.load() if cfg.resume_run_id else None
    if existing is not None:
        state = existing
        log.info(
            "Resume run_id=%s from phase=%s", state.run_id, state.phase
        )
    else:
        state = RunState(run_id=cfg.run_id, parser_id=cfg.parser_id)
        store.save(state)

    log.info(
        "Start throughput test run_id=%s parser_id=%s base_url=%s add=%s "
        "wait=%ss add_window=%ss remove_window=%ss dry_run=%s restore_only=%s out=%s",
        cfg.run_id,
        cfg.parser_id,
        cfg.base_url,
        cfg.add_count,
        cfg.wait_recovery_sec,
        cfg.add_window_sec,
        cfg.remove_window_sec,
        cfg.dry_run,
        cfg.restore_only,
        cfg.run_dir,
    )
    if "oboyma.ai" in cfg.base_url or "web.oboyma" in cfg.base_url:
        log.warning(
            "base_url=%s похож на публичный домен. На vps-104 используйте "
            "http://127.0.0.1:8100 — иначе nginx hairpin даёт 404/503.",
            cfg.base_url,
        )

    stop_flag = cfg.run_dir / "STOP"
    if stop_flag.exists() and not cfg.resume_run_id:
        stop_flag.unlink()

    (cfg.run_dir / "config.json").write_text(
        json.dumps(
            {
                "run_id": cfg.run_id,
                "parser_id": cfg.parser_id,
                "base_url": cfg.base_url,
                "add_count": cfg.add_count,
                "wait_recovery_sec": cfg.wait_recovery_sec,
                "add_window_sec": cfg.add_window_sec,
                "remove_window_sec": cfg.remove_window_sec,
                "chunk_size": cfg.chunk_size,
                "skip_n8n": cfg.skip_n8n,
                "skip_producers": cfg.skip_producers,
                "dry_run": cfg.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    errors = ErrorSink(cfg.run_dir / "errors.jsonl")
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        log.warning("Stop requested (signal/STOP) — после текущей итерации → restore")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_stop())

    async def watch_stop_file() -> None:
        while not stop_event.is_set():
            if stop_flag.exists():
                _request_stop()
                return
            await asyncio.sleep(1.0)

    db = ThroughputDb(cfg.pg_url)
    api = DiscoveryApi(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=cfg.http_timeout_sec,
        retries=cfg.http_retries,
        errors=errors,
    )

    orch: PhaseOrchestrator | None = None
    stop_watch_task: asyncio.Task | None = None

    try:
        await db.connect()
        stop_watch_task = asyncio.create_task(watch_stop_file())
        orch = PhaseOrchestrator(
            cfg=cfg,
            db=db,
            api=api,
            errors=errors,
            state=state,
            store=store,
            stop_event=stop_event,
        )
        await orch.run_all()
    except Exception as exc:  # noqa: BLE001
        log.exception("Fatal: %s", exc)
        errors.add(phase="harness", op="fatal", error=exc)
    finally:
        stop_event.set()
        if stop_watch_task:
            stop_watch_task.cancel()
            try:
                await stop_watch_task
            except asyncio.CancelledError:
                pass

        if orch is not None:
            try:
                await ensure_restore_on_exit(orch)
            except Exception as exc:  # noqa: BLE001
                log.exception("ensure_restore_on_exit: %s", exc)

            # финальный отчёт, если ещё не писали
            if orch.state.phase not in ("report", "done") or not (
                orch.cfg.run_dir / "report.md"
            ).exists():
                try:
                    orch.state.mark_phase("report")
                    await orch.phase_report()
                    orch.state.mark_phase("done")
                    orch._save()
                except Exception as exc:  # noqa: BLE001
                    log.exception("Final report failed: %s", exc)
                    errors.add(phase="report", op="final", error=exc)

        await api.aclose()
        await db.close()

    log.info("Done. errors=%s out=%s", errors.count(), cfg.run_dir)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(async_main(argv)))


if __name__ == "__main__":
    main()
