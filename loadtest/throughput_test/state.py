"""Персистентное состояние прогона (resume + аварийное восстановление)."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunState:
    run_id: str
    phase: str = "preflight"
    parser_id: str = ""
    n8n_deactivated: list[dict[str, Any]] = field(default_factory=list)
    producers_stopped: list[str] = field(default_factory=list)
    queue_paused_count: int = 0
    queue_backup_file: str = "queue_backup.json"
    added_channels: list[str] = field(default_factory=list)
    add_task_ids: list[int] = field(default_factory=list)
    remove_task_ids: list[int] = field(default_factory=list)
    restore_result: dict[str, Any] = field(default_factory=dict)
    phase_timestamps: dict[str, str] = field(default_factory=dict)
    phase_results: dict[str, Any] = field(default_factory=dict)
    completed_phases: list[str] = field(default_factory=list)
    needs_restore: bool = False
    updated_at: str = field(default_factory=_now)

    def mark_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_timestamps.setdefault(phase, _now())
        self.updated_at = _now()

    def complete_phase(self, phase: str) -> None:
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunState":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in raw.items() if k in known}
        return cls(**filtered)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> RunState | None:
        if not self.path.is_file():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return RunState.from_dict(raw)

    def save(self, state: RunState) -> None:
        state.updated_at = _now()
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            tmp.replace(self.path)
