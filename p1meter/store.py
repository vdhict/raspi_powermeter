"""SQLite storage: one row per time bucket (default 10 s)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts          INTEGER PRIMARY KEY,  -- bucket start, unix seconds (UTC)
    seconds     INTEGER NOT NULL,     -- bucket length
    import_w    REAL NOT NULL,        -- average power taken from the grid
    export_w    REAL NOT NULL,        -- average power fed into the grid
    l1_w        REAL,                 -- average net power per phase
    l2_w        REAL,
    l3_w        REAL,
    import_kwh  REAL NOT NULL,        -- meter counters at end of bucket
    export_kwh  REAL NOT NULL
) WITHOUT ROWID;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    # WAL lets the web server read while the logger writes and keeps the
    # number of SD-card syncs low.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


class Writer:
    """Buffers rows and writes them in batches to spare the SD card."""

    def __init__(self, path: str | Path, batch: int = 6):
        self._db = connect(path)
        self._batch = batch
        self._pending: list[tuple] = []
        self._lock = threading.Lock()

    def add(self, row: tuple) -> None:
        with self._lock:
            self._pending.append(row)
            if len(self._pending) >= self._batch:
                self._flush()

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            with self._db:
                self._db.executemany(
                    "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?)",
                    self._pending,
                )
            self._pending.clear()

    def close(self) -> None:
        self.flush()
        self._db.close()


def series(db: sqlite3.Connection, start: int, end: int, resolution: int) -> list[sqlite3.Row]:
    """Average import/export power per `resolution` seconds in [start, end)."""
    return db.execute(
        """
        SELECT (ts / :res) * :res AS t,
               SUM(import_w * seconds) / SUM(seconds) AS import_w,
               SUM(export_w * seconds) / SUM(seconds) AS export_w,
               SUM(l1_w * seconds) / SUM(seconds) AS l1_w,
               SUM(l2_w * seconds) / SUM(seconds) AS l2_w,
               SUM(l3_w * seconds) / SUM(seconds) AS l3_w,
               SUM(seconds) AS seconds
        FROM samples WHERE ts >= :start AND ts < :end
        GROUP BY t ORDER BY t
        """,
        {"res": resolution, "start": start, "end": end},
    ).fetchall()


def days(db: sqlite3.Connection, start: int = 0, end: int = 2**62) -> list[sqlite3.Row]:
    """Energy per local calendar day, from the bucket averages.

    `coverage` is the fraction of the day for which data exists.
    """
    return db.execute(
        """
        SELECT date(ts, 'unixepoch', 'localtime') AS day,
               SUM(import_w * seconds) / 3.6e6 AS import_kwh,
               SUM(export_w * seconds) / 3.6e6 AS export_kwh,
               MAX(import_w) AS peak_import_w,
               MAX(export_w) AS peak_export_w,
               SUM(seconds) / 86400.0 AS coverage
        FROM samples WHERE ts >= ? AND ts < ?
        GROUP BY day ORDER BY day
        """,
        (start, end),
    ).fetchall()


def span(db: sqlite3.Connection) -> tuple[int, int] | None:
    row = db.execute("SELECT MIN(ts), MAX(ts) FROM samples").fetchone()
    return (row[0], row[1]) if row[0] is not None else None
