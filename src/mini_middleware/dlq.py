"""Dead-letter store. Messages land here when retrying cannot help, or has been exhausted."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from . import db

# Reason codes. Operators filter and alert on these.
MALFORMED_XML = "MALFORMED_XML"
VALIDATION_FAILED = "VALIDATION_FAILED"
KEY_REUSE_CONFLICT = "KEY_REUSE_CONFLICT"
PERMANENT_REJECTION = "PERMANENT_REJECTION"
RETRIES_EXHAUSTED = "RETRIES_EXHAUSTED"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class DeadLetterStore:
    def __init__(self, db_path) -> None:
        self.db_path = db_path
        db.init_db(db_path)

    def add(
        self,
        *,
        key: str | None,
        correlation_id: str,
        source: str,
        reason: str,
        detail: str,
        attempts: int,
        payload: str,
    ) -> int:
        now = _now()
        with db.transaction(self.db_path) as c:
            cur = c.execute(
                "INSERT INTO dead_letters(idempotency_key, correlation_id, source, reason, detail,"
                " attempts, payload, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (key, correlation_id, source, reason, detail, attempts, payload, now, now),
            )
            return int(cur.lastrowid)

    def record_replay_failure(
        self, dlq_id: int, *, correlation_id: str, reason: str, detail: str, attempts: int
    ) -> None:
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE dead_letters SET replay_count=replay_count+1, reason=?, detail=?,"
                " attempts=attempts+?, correlation_id=?, updated_at=? WHERE id=?",
                (reason, detail, attempts, correlation_id, _now(), dlq_id),
            )

    def mark_replayed(self, dlq_id: int, resolution: str, correlation_id: str) -> None:
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE dead_letters SET status='REPLAYED', replay_count=replay_count+1,"
                " resolution=?, correlation_id=?, updated_at=? WHERE id=?",
                (resolution, correlation_id, _now(), dlq_id),
            )

    def discard(self, dlq_id: int, reason: str) -> None:
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE dead_letters SET status='DISCARDED', resolution=?, updated_at=?"
                " WHERE id=? AND status='DEAD'",
                (reason, _now(), dlq_id),
            )

    def get(self, dlq_id: int) -> dict[str, Any] | None:
        with db.session(self.db_path) as c:
            row = c.execute("SELECT * FROM dead_letters WHERE id=?", (dlq_id,)).fetchone()
        return dict(row) if row else None

    def list(self, status: str | None = "DEAD") -> list[dict[str, Any]]:
        with db.session(self.db_path) as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM dead_letters WHERE status=? ORDER BY id", (status,)
                ).fetchall()
            else:
                rows = c.execute("SELECT * FROM dead_letters ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def count_open(self) -> int:
        with db.session(self.db_path) as c:
            return int(c.execute("SELECT COUNT(*) FROM dead_letters WHERE status='DEAD'").fetchone()[0])
