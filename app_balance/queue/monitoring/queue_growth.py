"""G4 — in-memory трекер роста очереди для правила queue_growth."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class QueueGrowthTracker:
    """Хранит историю queue.total в скользящем окне (только in-process)."""

    window_seconds: int
    _samples: deque[tuple[datetime, int]] = field(default_factory=deque)

    def record(self, at: datetime, queue_total: int) -> None:
        self._samples.append((at, queue_total))
        self._prune(at)

    def _prune(self, now: datetime) -> None:
        cutoff = now.timestamp() - self.window_seconds
        while self._samples and self._samples[0][0].timestamp() < cutoff:
            self._samples.popleft()

    def growth_percent(self) -> float | None:
        """Рост текущего total относительно самой ранней точки в окне."""
        if len(self._samples) < 2:
            return None
        baseline = self._samples[0][1]
        current = self._samples[-1][1]
        if baseline <= 0:
            return 100.0 if current > 0 else 0.0
        return (current - baseline) / baseline * 100.0

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
