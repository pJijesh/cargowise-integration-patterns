"""SQLite helpers. One short-lived connection per operation keeps threads independent."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    idempotency_key TEXT PRIMARY KEY,
    fingerprint     TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    status          TEXT NOT NULL,      -- IN_PROGRESS | DELIVERED | DEAD_LETTERED | STALE
    attempts        INTEGER NOT NULL DEFAULT 0,
    reference       TEXT,
    sequence        INTEGER,
    lease_expires   REAL,
    last_error      TEXT,
    first_seen      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shipment_state (
    reference      TEXT PRIMARY KEY,
    last_sequence  INTEGER NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key  TEXT,
    correlation_id   TEXT NOT NULL,
    source           TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    payload          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'DEAD',   -- DEAD | REPLAYED | DISCARDED
    replay_count     INTEGER NOT NULL DEFAULT 0,
    resolution       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dead_letters_status ON dead_letters(status);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10, isolation_level=None)  # autocommit; explicit BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path: str | Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def session(path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE takes the write lock up front, which makes claim-then-update atomic."""
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()
