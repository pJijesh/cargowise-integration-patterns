"""Idempotency store: records what has been seen and what has been delivered."""

from __future__ import annotations

import datetime as _dt
import time
from enum import Enum
from typing import Callable

from . import db


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(timespec="seconds")


class BeginStatus(str, Enum):
    NEW = "NEW"              # first time this key has been seen
    RESUMED = "RESUMED"      # seen before but never completed (crash, dead letter, replay)
    DUPLICATE = "DUPLICATE"  # already delivered, or deliberately superseded
    IN_FLIGHT = "IN_FLIGHT"  # another worker holds an unexpired lease
    CONFLICT = "CONFLICT"    # same key, different content


class IdempotencyStore:
    def __init__(
        self,
        db_path,
        lease_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path
        self.lease_seconds = lease_seconds
        self._clock = clock
        db.init_db(db_path)

    def begin(self, key: str, fingerprint: str, correlation_id: str) -> BeginStatus:
        """Atomically claim `key` for processing and say what the caller should do."""
        now = self._clock()
        lease = now + self.lease_seconds
        with db.transaction(self.db_path) as c:
            row = c.execute("SELECT * FROM messages WHERE idempotency_key=?", (key,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO messages(idempotency_key, fingerprint, correlation_id, status,"
                    " lease_expires, first_seen, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (key, fingerprint, correlation_id, "IN_PROGRESS", lease, _iso(now), _iso(now)),
                )
                return BeginStatus.NEW
            if row["fingerprint"] != fingerprint:
                return BeginStatus.CONFLICT
            if row["status"] in ("DELIVERED", "STALE"):
                return BeginStatus.DUPLICATE
            if row["status"] == "IN_PROGRESS" and (row["lease_expires"] or 0) > now:
                return BeginStatus.IN_FLIGHT
            c.execute(
                "UPDATE messages SET status='IN_PROGRESS', lease_expires=?, correlation_id=?,"
                " updated_at=? WHERE idempotency_key=?",
                (lease, correlation_id, _iso(now), key),
            )
            return BeginStatus.RESUMED

    def last_sequence(self, reference: str) -> int:
        with db.session(self.db_path) as c:
            row = c.execute(
                "SELECT last_sequence FROM shipment_state WHERE reference=?", (reference,)
            ).fetchone()
        return int(row["last_sequence"]) if row else 0

    def is_stale(self, reference: str, sequence: int) -> bool:
        """True if a newer message for this shipment has already been delivered."""
        return sequence <= self.last_sequence(reference)

    def mark_delivered(self, key: str, attempts: int, reference: str, sequence: int) -> None:
        now = self._clock()
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE messages SET status='DELIVERED', attempts=attempts+?, reference=?,"
                " sequence=?, lease_expires=NULL, last_error=NULL, updated_at=?"
                " WHERE idempotency_key=?",
                (attempts, reference, sequence, _iso(now), key),
            )
            c.execute(
                "INSERT INTO shipment_state(reference, last_sequence, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(reference) DO UPDATE SET"
                " last_sequence=MAX(last_sequence, excluded.last_sequence),"
                " updated_at=excluded.updated_at",
                (reference, sequence, _iso(now)),
            )

    def mark_dead(self, key: str, attempts: int, error: str) -> None:
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE messages SET status='DEAD_LETTERED', attempts=attempts+?, lease_expires=NULL,"
                " last_error=?, updated_at=? WHERE idempotency_key=?",
                (attempts, error, _iso(self._clock()), key),
            )

    def mark_stale(self, key: str, reference: str, sequence: int) -> None:
        with db.transaction(self.db_path) as c:
            c.execute(
                "UPDATE messages SET status='STALE', reference=?, sequence=?, lease_expires=NULL,"
                " updated_at=? WHERE idempotency_key=?",
                (reference, sequence, _iso(self._clock()), key),
            )

    def forget_dead(self, key: str) -> None:
        """Remove a DEAD_LETTERED record so a corrected message can reuse the key."""
        with db.transaction(self.db_path) as c:
            c.execute(
                "DELETE FROM messages WHERE idempotency_key=? AND status='DEAD_LETTERED'", (key,)
            )

    def get(self, key: str) -> dict | None:
        with db.session(self.db_path) as c:
            row = c.execute("SELECT * FROM messages WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def delivered(self) -> list[dict]:
        with db.session(self.db_path) as c:
            rows = c.execute(
                "SELECT idempotency_key, reference, sequence FROM messages"
                " WHERE status='DELIVERED' ORDER BY first_seen, idempotency_key"
            ).fetchall()
        return [dict(r) for r in rows]

    def stuck(self) -> list[dict]:
        """IN_PROGRESS rows whose lease has expired: a worker died mid-flight."""
        with db.session(self.db_path) as c:
            rows = c.execute(
                "SELECT idempotency_key, correlation_id, updated_at FROM messages"
                " WHERE status='IN_PROGRESS' AND lease_expires < ?",
                (self._clock(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with db.session(self.db_path) as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
