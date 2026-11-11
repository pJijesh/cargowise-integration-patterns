"""Tiny in-process counters. A real deployment would export these to a metrics system."""

from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._counters.items()))

    def render_text(self) -> str:
        """Prometheus-style exposition text (counters only)."""
        lines = []
        for name, value in self.snapshot().items():
            lines.append(f"# TYPE mm_{name} counter")
            lines.append(f"mm_{name} {value}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()
