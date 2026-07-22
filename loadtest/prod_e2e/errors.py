"""Учёт ошибок: тест не прерывается при сбое отдельной операции."""

from __future__ import annotations

import json
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ErrorRecord:
    ts: str
    phase: str
    user_key: str | None
    op: str
    error: str
    detail: dict[str, Any] = field(default_factory=dict)


class ErrorSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._items: list[ErrorRecord] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("", encoding="utf-8")

    def add(
        self,
        *,
        phase: str,
        op: str,
        error: BaseException | str,
        user_key: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(error, BaseException):
            msg = f"{type(error).__name__}: {error}"
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            d = dict(detail or {})
            d["traceback"] = tb[-4000:]
        else:
            msg = str(error)
            d = dict(detail or {})
        rec = ErrorRecord(
            ts=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            user_key=user_key,
            op=op,
            error=msg,
            detail=d,
        )
        with self._lock:
            self._items.append(rec)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def all(self) -> list[ErrorRecord]:
        with self._lock:
            return list(self._items)

    def count(self) -> int:
        with self._lock:
            return len(self._items)
