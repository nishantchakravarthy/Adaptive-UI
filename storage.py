"""
storage.py — Local session history (SQLite).

Each time the state changes, the finished segment is stored: when it started
and ended, which state it was, which state followed, the engine's confidence
and the foreground app. Nothing about what was typed is ever stored.

Python's sqlite3 ties a connection to the thread that created it, so one
writer thread owns the write connection and receives work through a queue;
readers open their own short-lived connections. WAL mode lets reads run while
the writer is busy, so the UI loop never waits on the database.
"""

import logging
import os
import queue
import sqlite3
import threading
import time

from config import config_dir

logger = logging.getLogger("AdaptiveUI.Storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started     REAL NOT NULL,
    ended       REAL NOT NULL,
    state       TEXT NOT NULL,
    next_state  TEXT,
    confidence  REAL,
    app         TEXT
);
CREATE INDEX IF NOT EXISTS idx_segments_started ON segments(started);
"""


def default_db_path():
    return os.path.join(config_dir(), "sessions.db")


class SessionStore:
    """Thread-safe store; all writes happen on one background thread."""

    def __init__(self, path=None, retention_days=90):
        self.path = path or default_db_path()
        self.retention_days = retention_days
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._queue = queue.Queue()
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._writer, name="SessionStore", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._error:
            raise self._error

    # ─── Writer thread ───────────────────────────────────────────────────────

    def _writer(self):
        try:
            conn = sqlite3.connect(self.path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            self._purge(conn)  # before reporting ready, so reads never see expired rows
        except Exception as e:  # surfaced to the constructor
            self._error = e
            self._ready.set()
            return
        self._ready.set()

        while True:
            item = self._queue.get()
            try:
                if item is None:
                    break
                kind, payload = item
                if kind == "segment":
                    conn.execute(
                        "INSERT INTO segments (started, ended, state, next_state, confidence, app) "
                        "VALUES (?, ?, ?, ?, ?, ?)", payload)
                    conn.commit()
                elif kind == "delete_all":
                    conn.execute("DELETE FROM segments")
                    conn.commit()
                elif kind == "purge":
                    self._purge(conn)
            except Exception:
                logger.exception("Session store write failed")
            finally:
                self._queue.task_done()
        conn.close()

    def _purge(self, conn):
        if not self.retention_days:
            return
        cutoff = time.time() - self.retention_days * 86400
        conn.execute("DELETE FROM segments WHERE ended < ?", (cutoff,))
        conn.commit()

    # ─── Public API (any thread) ─────────────────────────────────────────────

    def record(self, started, ended, state, next_state=None, confidence=None, app=None):
        """Queue one finished segment. Zero-length or reversed segments are ignored."""
        if ended <= started or not state:
            return
        self._queue.put(("segment", (float(started), float(ended), str(state),
                                     next_state, confidence, app)))

    def delete_all(self):
        self._queue.put(("delete_all", None))

    def purge(self):
        self._queue.put(("purge", None))

    def flush(self):
        """Block until every queued write has been applied."""
        self._queue.join()

    def close(self):
        self.flush()
        self._queue.put(None)
        self._thread.join(timeout=5)

    def segments_between(self, start, end):
        """Segments overlapping [start, end), oldest first, as dicts (own read connection)."""
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT started, ended, state, next_state, confidence, app FROM segments "
                "WHERE ended > ? AND started < ? ORDER BY started", (start, end)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
