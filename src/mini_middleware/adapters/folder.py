"""Folder-drop inbound adapter.

Layout under the base directory:

    inbox/       senders drop finished files here
    processing/  a file is claimed by an atomic rename into this folder
    processed/   delivered, or recognised as a duplicate or stale update
    failed/      dead-lettered (the payload is also held in the dead-letter store)

Senders must write to a temporary name and rename to `*.xml` when complete. The
`settle_seconds` guard is a second line of defence against half-written files.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from ..logging_setup import log_event
from ..pipeline import Outcome, Pipeline, Result

log = logging.getLogger("mini_middleware.adapters.folder")


class FolderAdapter:
    def __init__(
        self,
        base_dir: str | Path,
        pipeline: Pipeline,
        settle_seconds: float = 0.5,
        max_bytes: int = 1_000_000,
    ) -> None:
        self.base = Path(base_dir)
        self.pipeline = pipeline
        self.settle_seconds = settle_seconds
        self.max_bytes = max_bytes
        self.inbox = self.base / "inbox"
        self.processing = self.base / "processing"
        self.processed = self.base / "processed"
        self.failed = self.base / "failed"
        for d in (self.inbox, self.processing, self.processed, self.failed):
            d.mkdir(parents=True, exist_ok=True)

    def recover_orphans(self) -> int:
        """Return files stranded in processing/ (a crash mid-run) to the inbox."""
        moved = 0
        for p in self.processing.glob("*.xml"):
            os.replace(p, self.inbox / p.name)
            moved += 1
        if moved:
            log_event(log, "orphans_recovered", logging.WARNING, count=moved)
        return moved

    def scan_once(self) -> list[tuple[str, Result | None]]:
        results: list[tuple[str, Result | None]] = []
        # Sorted by name so a sender can control order with sortable file names.
        for path in sorted(self.inbox.glob("*.xml")):
            try:
                if time.time() - path.stat().st_mtime < self.settle_seconds:
                    continue
                claimed = self.processing / path.name
                os.replace(path, claimed)  # atomic claim; losers of a race get FileNotFoundError
            except FileNotFoundError:
                continue
            results.append((path.name, self._handle(claimed)))
        return results

    def _handle(self, claimed: Path) -> Result | None:
        if claimed.stat().st_size > self.max_bytes:
            log_event(log, "file_rejected_too_large", logging.ERROR, file=claimed.name)
            self.pipeline.metrics.inc("oversize_rejected_total")
            self._move(claimed, self.failed)
            return None
        try:
            result = self.pipeline.process(claimed.read_bytes(), source=f"folder:{claimed.name}")
        except Exception:  # a bug must not lose the file or kill the watcher
            log.exception("unexpected_error", extra={"fields": {"file": claimed.name}})
            self._move(claimed, self.failed)
            return None
        target = self.failed if result.outcome is Outcome.DEAD_LETTERED else self.processed
        self._move(claimed, target)
        return result

    @staticmethod
    def _move(src: Path, dest_dir: Path) -> None:
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}.{int(time.time() * 1000)}{src.suffix}"
        os.replace(src, dest)

    def watch(self, interval: float = 2.0, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.recover_orphans()
        log_event(log, "watching", folder=str(self.inbox), interval_s=interval)
        while not stop.is_set():
            self.scan_once()
            stop.wait(interval)
