from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def resolve_sessions_dir() -> Path | None:
    """Каталог личных сессий. Без явной настройки на машине разработчика ничего не создаёт."""
    raw = os.getenv("CLIENT_SESSIONS_DIR", "").strip()
    if raw:
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path
    default = Path("/app/client-sessions")
    if Path("/app").is_dir():
        default.mkdir(parents=True, exist_ok=True)
        return default
    return None


class FileSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def session_path(self, remote_account_id: str) -> Path:
        safe = self._safe_id(remote_account_id)
        return self.root / safe

    def _meta_path(self, remote_account_id: str) -> Path:
        return self.session_path(remote_account_id).with_suffix(".json")

    def _safe_id(self, remote_account_id: str) -> str:
        if not ACCOUNT_ID_RE.fullmatch(remote_account_id):
            raise ValueError("Некорректный идентификатор аккаунта")
        return remote_account_id

    def get(self, remote_account_id: str) -> dict[str, Any] | None:
        path = self._meta_path(remote_account_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, meta: dict[str, Any]) -> None:
        remote_account_id = str(meta["remote_account_id"])
        path = self._meta_path(remote_account_id)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure(self, remote_account_id: str) -> dict[str, Any]:
        current = self.get(remote_account_id)
        if current:
            return current
        meta = {
            "remote_account_id": remote_account_id,
            "status": "pending",
            "telegram_user_id": None,
            "username": None,
            "display_name": None,
            "qr_session_id": None,
            "watched_peers": [],
            "cursors": {},
            "idempotency": {},
        }
        self.save(meta)
        return meta

    def list_watched(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("status") == "active" and meta.get("watched_peers"):
                rows.append(meta)
        return rows

    def remember_idempotency(self, remote_account_id: str, key: str, payload: dict[str, Any]) -> None:
        meta = self.ensure(remote_account_id)
        bucket = meta.setdefault("idempotency", {})
        bucket[key] = payload
        if len(bucket) > 200:
            for old in list(bucket)[:-200]:
                bucket.pop(old, None)
        self.save(meta)

    def idempotency(self, remote_account_id: str, key: str) -> dict[str, Any] | None:
        meta = self.get(remote_account_id) or {}
        found = (meta.get("idempotency") or {}).get(key)
        return found if isinstance(found, dict) else None
