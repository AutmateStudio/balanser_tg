"""D3 — восстановление SessionClump из parser_jobs.json для queue-worker (без FastAPI _jobs)."""
from __future__ import annotations

import logging
import os

from discovery_api.parser_store import (
    is_persistence_enabled,
    load_persisted_jobs,
    normalize_persisted_record,
)
from discovery_api.session_registry import get_clump, get_or_create_clump

log = logging.getLogger(__name__)


def env_telegram_configured() -> bool:
    return bool(os.getenv("API_ID", "").strip() and os.getenv("API_HASH", "").strip())


async def restore_all_clumps_from_store() -> int:
    """Поднимает clump'ы из JSON-хранилища. Возвращает число успешно восстановленных."""
    if not is_persistence_enabled():
        log.info("clump_bootstrap: persistence отключён (PARSER_PERSISTENCE_ENABLED)")
        return 0
    if not env_telegram_configured():
        log.warning(
            "clump_bootstrap: пропуск — не заданы API_ID и/или API_HASH"
        )
        return 0

    restored = 0
    for rec in load_persisted_jobs():
        rec = normalize_persisted_record(rec)
        parser_id = rec.get("parser_id")
        if not isinstance(parser_id, str) or not parser_id:
            continue
        if get_clump(parser_id) is not None:
            continue

        session_list = rec.get("session_name_list")
        if not isinstance(session_list, list) or not session_list:
            log.warning("clump_bootstrap: пропуск записи без session_name_list: %s", rec)
            continue

        webhook_url = rec.get("webhook_url")
        channel_list = rec.get("channel_list")
        if not webhook_url or not isinstance(channel_list, list):
            log.warning("clump_bootstrap: пропуск некорректной записи: %s", rec)
            continue

        ch_list = [str(x) for x in channel_list]
        if not ch_list:
            log.warning("clump_bootstrap: пропуск записи без каналов: %s", parser_id)
            continue

        try:
            clump = await get_or_create_clump(
                parser_id,
                [str(s) for s in session_list],
                str(webhook_url),
                clump_name=str(rec.get("clump_name") or parser_id),
            )
            clump.restore_from_record(rec)
            await clump.start()
            restored += 1
            log.info(
                "clump_bootstrap: восстановлен parser_id=%s sessions=%s",
                parser_id,
                len(session_list),
            )
        except Exception:
            log.exception(
                "clump_bootstrap: не удалось восстановить clump (id=%s)", parser_id
            )
    return restored
