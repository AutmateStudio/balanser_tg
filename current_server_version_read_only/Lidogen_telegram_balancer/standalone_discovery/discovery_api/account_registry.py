"""Реестр всех Telegram-аккаунтов: скан SESSIONS_DIR + merge с runtime clump."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from discovery_api.account_store import (
    delete_account as store_delete_account,
    get_account,
    list_accounts,
    upsert_account,
)

log = __import__("logging").getLogger(__name__)


def sessions_dir() -> str:
    """Каталог `.session`-файлов (читается из env при каждом вызове)."""
    return os.getenv("SESSIONS_DIR", "/app/sessions")


def normalize_session_name(session_name: str) -> str:
    """Каноническое имя аккаунта (basename без .session)."""
    base = (session_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if base.endswith(".session"):
        base = base[: -len(".session")]
    return base


def default_display_name(session_name: str) -> str:
    return normalize_session_name(session_name) or session_name


def session_file_path(session_name: str) -> str:
    """Абсолютный путь к `.session`-файлу в SESSIONS_DIR."""
    name = normalize_session_name(session_name)
    return os.path.join(sessions_dir(), f"{name}.session")


def session_file_exists(session_name: str) -> bool:
    return os.path.isfile(session_file_path(session_name))


def scan_sessions_dir() -> List[str]:
    """Имена аккаунтов по файлам *.session в SESSIONS_DIR."""
    if not os.path.isdir(sessions_dir()):
        return []
    out: List[str] = []
    for entry in os.listdir(sessions_dir()):
        if entry.endswith(".session"):
            out.append(entry[: -len(".session")])
    return sorted(out)


def sync_accounts_from_disk(*, source: str = "import") -> int:
    """Upsert записей account_store для каждого .session на диске."""
    count = 0
    for name in scan_sessions_dir():
        existing = get_account(name)
        if existing is None:
            upsert_account(
                name,
                display_name=default_display_name(name),
                source=source,
            )
            count += 1
    return count


def register_account_after_qr(session_name: str) -> Dict[str, Any]:
    """Регистрация аккаунта после успешного QR (без clump)."""
    name = normalize_session_name(session_name)
    return upsert_account(
        name,
        display_name=default_display_name(name),
        source="qr",
    )


def build_runtime_index(
    jobs: Dict[str, Any],
) -> Dict[str, Tuple[str, Any, Dict[str, Any]]]:
    """session_name -> (parser_id, clump, summary_dict)."""
    index: Dict[str, Tuple[str, Any, Dict[str, Any]]] = {}
    for pid, job in jobs.items():
        clump = job.clump
        for summary in clump.list_account_summaries():
            sn = summary["session_name"]
            index[sn] = (pid, clump, summary)
            norm = normalize_session_name(sn)
            if norm != sn:
                index[norm] = (pid, clump, {**summary, "session_name": sn})
    return index


def eff_channel_limit_info(
    session_name: str,
    clump_limit: int,
) -> Tuple[int, str]:
    """(effective_limit, source: account|clump|env)."""
    rec = get_account(normalize_session_name(session_name))
    if rec and rec.get("max_channels") is not None:
        return int(rec["max_channels"]), "account"
    return clump_limit, "clump"


def is_admin_blocked(session_name: str) -> bool:
    rec = get_account(normalize_session_name(session_name))
    return bool(rec and rec.get("admin_blocked"))


def list_all_accounts_merged(
    jobs: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Полный список для админки: store + disk + runtime."""
    sync_accounts_from_disk()
    runtime = build_runtime_index(jobs)
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []

    def _append(name: str, store_rec: Optional[Dict[str, Any]]) -> None:
        norm = normalize_session_name(name)
        if norm in seen:
            return
        seen.add(norm)
        rec = store_rec or get_account(norm)
        if rec is None and session_file_exists(norm):
            rec = upsert_account(norm, display_name=default_display_name(norm), source="import")

        rt = runtime.get(name) or runtime.get(norm)
        clump_limit = 500
        if rt:
            _, _, summary = rt
            clump_limit = int(summary.get("max_channels_per_session") or clump_limit)

        eff_limit, limit_source = eff_channel_limit_info(norm, clump_limit)

        row: Dict[str, Any] = {
            "session_name": norm,
            "display_name": (rec or {}).get("display_name") or default_display_name(norm),
            "description": (rec or {}).get("description") or "",
            "max_channels": (rec or {}).get("max_channels"),
            "effective_max_channels": eff_limit,
            "limit_source": limit_source,
            "admin_blocked": bool((rec or {}).get("admin_blocked")),
            "block_reason": (rec or {}).get("block_reason"),
            "source": (rec or {}).get("source") or "import",
            "session_file_exists": session_file_exists(norm),
            "in_clump": rt is not None,
            "parser_id": rt[0] if rt else None,
            "clump_name": rt[2].get("clump_name") if rt else None,
            "status": rt[2].get("status", "offline") if rt else "offline",
            "banned": rt[2].get("banned", False) if rt else False,
            "ban_reason": rt[2].get("ban_reason") if rt else None,
            "flood_remaining_seconds": rt[2].get("flood_remaining_seconds") if rt else None,
            "connected": rt[2].get("connected", False) if rt else False,
            "running": rt[2].get("running", False) if rt else False,
            "channel_count": rt[2].get("channel_count", 0) if rt else 0,
        }
        result.append(row)

    for rec in list_accounts():
        _append(rec["session_name"], rec)
    for name in scan_sessions_dir():
        _append(name, get_account(name))

    result.sort(key=lambda r: r["session_name"])
    return result


def delete_account_full(session_name: str) -> None:
    """Удаляет запись из store и .session файл."""
    norm = normalize_session_name(session_name)
    path = session_file_path(norm)
    if os.path.isfile(path):
        os.remove(path)
    store_delete_account(norm)
