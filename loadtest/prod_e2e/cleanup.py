"""Cleanup после loadtest: detach проектов, soft-cancel задач, optional remove."""

from __future__ import annotations

import logging
from typing import Any

from .api import DiscoveryApi
from .config import Config
from .db import Db, normalize_ref
from .errors import ErrorSink
from .seed import SeedResult

log = logging.getLogger("loadtest.cleanup")


async def cleanup_run(
    *,
    cfg: Config,
    db: Db,
    api: DiscoveryApi,
    seed: SeedResult,
    errors: ErrorSink,
) -> dict[str, Any]:
    if cfg.skip_cleanup:
        log.info("Cleanup пропущен (--skip-cleanup)")
        return {"skipped": True}

    summary: dict[str, Any] = {"cancelled_tasks": 0, "projects_archived": 0, "remove_calls": 0}

    # 1) soft-cancel задач по всем refs
    all_refs = sorted({r for u in seed.users for r in u.refs})
    try:
        n = await db.cancel_tasks_for_refs(
            parser_id=cfg.parser_id,
            refs=all_refs,
        )
        summary["cancelled_tasks"] = n
        log.info("Cancelled tasks: %s", n)
    except Exception as exc:  # noqa: BLE001
        errors.add(phase="cleanup", op="cancel_tasks", error=exc)

    # 2) archive projects + disable links
    try:
        n = await db.deactivate_loadtest_projects(cfg.run_id)
        summary["projects_archived"] = n
        log.info("Archived projects: %s", n)
    except Exception as exc:  # noqa: BLE001
        errors.add(phase="cleanup", op="deactivate_projects", error=exc)

    # 3) optional remove-channels (best-effort, чанками) — unique only, shared осторожно
    # Shared каналы могут быть нужны другим проектам → remove только unique refs
    if not cfg.dry_run:
        unique_refs = sorted({r for u in seed.users for r in u.unique_refs})
        # дедуп
        seen: set[str] = set()
        uniq: list[str] = []
        for r in unique_refs:
            n = normalize_ref(r)
            if n and n not in seen:
                seen.add(n)
                uniq.append(r)
        chunk = 25
        for i in range(0, len(uniq), chunk):
            part = uniq[i : i + chunk]
            try:
                resp = await api.remove_channels(
                    cfg.parser_id,
                    part,
                    phase="cleanup",
                    user_key="cleanup",
                )
                summary["remove_calls"] += 1
                if resp.get("_error"):
                    errors.add(
                        phase="cleanup",
                        op="remove-channels",
                        error=str(resp.get("body")),
                        detail={"chunk": len(part)},
                    )
            except Exception as exc:  # noqa: BLE001
                errors.add(phase="cleanup", op="remove-channels", error=exc)

    return summary
