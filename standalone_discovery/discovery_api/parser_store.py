"""Персистентное хранение конфигурации Telegram-парсеров (clump) для восстановления после
перезапуска процесса/контейнера.

Хранится список записей в JSON (`data/parser_jobs.json` по умолчанию).
Запись удаляется при `parser/stop` или `DELETE /parser/{id}`; при изменении
списка каналов файл перезаписывается.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, List, Optional

from discovery_api.session_registry import SessionClump

log = __import__("logging").getLogger(__name__)

DEFAULT_FILENAME = "parser_jobs.json"
SCHEMA_VERSION = 3


class ParserStoreCorruptedError(RuntimeError):
    """Файл хранилища СУЩЕСТВУЕТ, но не читается/не парсится/пуст.

    Отдельно от легитимного случая "файла нет" (первый запуск, restored=0 —
    это нормально). Если файл есть, но испорчен — это потеря состояния
    clump, и вызывающий код должен явно узнать об этом и залогировать как
    авария, а не тихо продолжить как будто restored=0 — штатное состояние.
    См. инцидент 2026-08-11 (docs/discovery-vps104.local.md в lidogen_site) —
    файл обнулился, restore тихо вернул [], авария не была замечена 6+ часов.
    """


def _store_path() -> str:
    raw = (os.getenv("PARSER_STORE_PATH") or "").strip()
    if raw:
        return raw
    base_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, DEFAULT_FILENAME)


def is_persistence_enabled() -> bool:
    return os.getenv("PARSER_PERSISTENCE_ENABLED", "1").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def load_persisted_jobs(*, store_path: Optional[str] = None) -> List[dict[str, Any]]:
    """Возвращает список сохранённых clump-записей.

    Если файла нет — возвращает [] (легитимно, например первый запуск).
    Если файл ЕСТЬ, но пуст/повреждён/не парсится — кидает
    ParserStoreCorruptedError, а не тихо возвращает []. Раньше оба случая
    были неотличимы, из-за чего потеря состояния (0-байтовый файл) выглядела
    как "восстанавливать нечего" вместо "авария, требуется вмешательство".
    """
    path = store_path or _store_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise ParserStoreCorruptedError(f"Не удалось открыть {path}: {e}") from e

    if not raw.strip():
        raise ParserStoreCorruptedError(
            f"{path} существует, но пуст (0 байт) — состояние clump потеряно"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParserStoreCorruptedError(f"Не удалось распарсить {path}: {e}") from e

    if not isinstance(data, list):
        raise ParserStoreCorruptedError(
            f"{path}: ожидался list верхнего уровня, получено {type(data).__name__}"
        )

    return [normalize_persisted_record(x) for x in data if isinstance(x, dict)]


def _atomic_write_json(path: str, payload: list[dict[str, Any]]) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".json.tmp", prefix="parser_jobs_", dir=parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=0)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_persisted_jobs(
    jobs: list[dict[str, Any]], *, store_path: Optional[str] = None
) -> None:
    if not is_persistence_enabled():
        return
    path = store_path or _store_path()
    _atomic_write_json(path, jobs)


def upsert_job(
    record: dict[str, Any], *, store_path: Optional[str] = None
) -> None:
    if not is_persistence_enabled():
        return
    pid = record.get("parser_id")
    if not pid:
        return
    try:
        jobs = load_persisted_jobs(store_path=store_path)
    except ParserStoreCorruptedError:
        log.critical(
            "parser_store: хранилище повреждено при upsert_job — перезаписываю "
            "с нуля только текущей записью (parser_id=%s), остальное состояние "
            "потеряно и не восстановится автоматически",
            pid,
            exc_info=True,
        )
        jobs = []
    others = [j for j in jobs if j.get("parser_id") != pid]
    others.append(normalize_persisted_record(dict(record)))
    save_persisted_jobs(others, store_path=store_path)


def delete_job(parser_id: str, *, store_path: Optional[str] = None) -> None:
    if not is_persistence_enabled():
        return
    try:
        jobs = load_persisted_jobs(store_path=store_path)
    except ParserStoreCorruptedError:
        log.critical(
            "parser_store: хранилище повреждено при delete_job(parser_id=%s) — "
            "нечего фильтровать, оставляю файл как есть для ручного разбора",
            parser_id,
            exc_info=True,
        )
        return
    filtered = [j for j in jobs if j.get("parser_id") != parser_id]
    if len(filtered) != len(jobs):
        save_persisted_jobs(filtered, store_path=store_path)


def normalize_persisted_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Приводит legacy-запись (один session_name) к schema v2."""
    out = dict(rec)
    session_list = out.get("session_name_list")
    legacy_name = out.get("session_name")
    if not isinstance(session_list, list) or not session_list:
        if legacy_name:
            out["session_name_list"] = [str(legacy_name)]
        else:
            out["session_name_list"] = []
    else:
        out["session_name_list"] = [str(x) for x in session_list]

    if not isinstance(out.get("assignments"), dict):
        out["assignments"] = {}

    if not isinstance(out.get("channel_list"), list):
        out["channel_list"] = []

    allowed = out.get("allowed_chat_ids")
    if isinstance(allowed, list):
        out["allowed_chat_ids"] = sorted(int(x) for x in allowed)
    else:
        out["allowed_chat_ids"] = []

    ref_map = out.get("ref_to_chat_id")
    if isinstance(ref_map, dict):
        clean_map: dict[str, int] = {}
        for k, v in ref_map.items():
            try:
                clean_map[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        out["ref_to_chat_id"] = clean_map
    else:
        out["ref_to_chat_id"] = {}

    out["schema_version"] = int(out.get("schema_version") or SCHEMA_VERSION)
    return out


def clump_to_record(clump: SessionClump, *, parser_id: str) -> dict[str, Any]:
    ref_to_chat_id: dict[str, int] = {}
    for pc in clump.parser_client_list:
        for ref, cid in pc.ref_to_chat_id.items():
            ref_to_chat_id[str(ref)] = int(cid)
    return {
        "parser_id": parser_id,
        "clump_name": clump.clump_name,
        "session_name_list": list(clump.session_name_list),
        "webhook_url": clump.webhook_url,
        "channel_list": clump.list_channels(),
        "assignments": dict(clump.assignments),
        "allowed_chat_ids": sorted(clump.all_allowed_chat_ids()),
        "ref_to_chat_id": ref_to_chat_id,
        "config": clump.config.overrides(),
        "account_meta": {k: dict(v) for k, v in clump.account_meta.items()},
        "schema_version": SCHEMA_VERSION,
    }


def job_to_record(
    *,
    parser_id: str,
    session_name: str,
    webhook_url: str,
    channel_list: list[str],
    allowed_chat_ids: set[int],
    clump_name: Optional[str] = None,
    assignments: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Legacy-совместимая запись для одной сессии."""
    assigns = dict(assignments) if assignments else {}
    if not assigns:
        for ch in channel_list:
            assigns[str(ch)] = session_name
    return normalize_persisted_record(
        {
            "parser_id": parser_id,
            "clump_name": clump_name,
            "session_name": session_name,
            "session_name_list": [session_name],
            "webhook_url": webhook_url,
            "channel_list": list(channel_list),
            "assignments": assigns,
            "allowed_chat_ids": sorted(int(x) for x in allowed_chat_ids),
            "schema_version": SCHEMA_VERSION,
        }
    )
