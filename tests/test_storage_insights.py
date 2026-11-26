import sqlite3
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from insights import daily_summary, weekly_view, day_bounds
from storage import SessionStore

DAY = date(2026, 12, 7)
T0, _ = day_bounds(DAY)
H9 = T0 + 9 * 3600  # 09:00 local


def seg(start, minutes, state, app=None):
    return {"started": start, "ended": start + minutes * 60, "state": state,
            "next_state": None, "confidence": 0.8, "app": app}


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "sessions.db"), retention_days=90)
    yield s
    s.close()


# ─── Storage ────────────────────────────────────────────────────────────────

def test_records_round_trip(store):
    store.record(H9, H9 + 60, "FOCUS", "LIGHT", 0.9, "code.exe")
    store.flush()
    rows = store.segments_between(H9 - 1, H9 + 61)
    assert rows == [{"started": H9, "ended": H9 + 60, "state": "FOCUS",
                     "next_state": "LIGHT", "confidence": 0.9, "app": "code.exe"}]


def test_ignores_empty_or_reversed_segments(store):
    store.record(H9, H9, "FOCUS")
    store.record(H9, H9 - 5, "FOCUS")
    store.record(H9, H9 + 5, "")
    store.flush()
    assert store.segments_between(0, 2e10) == []


def test_writes_from_many_threads(store):
    def worker(k):
        for i in range(50):
            store.record(H9 + k * 1000 + i, H9 + k * 1000 + i + 0.5, "ACTIVE")
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.flush()
    assert len(store.segments_between(0, 2e10)) == 200


def test_delete_all_empties_database(store):
    store.record(H9, H9 + 60, "FOCUS")
    store.delete_all()
    store.flush()
    assert store.segments_between(0, 2e10) == []


def test_retention_purges_old_rows(tmp_path):
    path = str(tmp_path / "s.db")
    s = SessionStore(path, retention_days=90)
    now = time.time()
    s.record(now - 100 * 86400, now - 100 * 86400 + 60, "FOCUS")
    s.record(now - 3600, now - 3500, "FOCUS")
    s.close()
    s2 = SessionStore(path, retention_days=90)  # purge runs on start
    s2.flush()
    assert len(s2.segments_between(0, 2e10)) == 1
    s2.close()


def test_uses_wal_mode(store):
    conn = sqlite3.connect(store.path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


# ─── Daily summary ──────────────────────────────────────────────────────────

def test_daily_summary_totals_and_streaks():
    segs = [
        seg(H9, 30, "FOCUS", "code.exe"),
        seg(H9 + 30 * 60, 1, "ACTIVE"),               # 1 min break: interruption
        seg(H9 + 31 * 60, 20, "FOCUS", "code.exe"),
        seg(H9 + 51 * 60, 10, "READING", "chrome.exe"),  # 10 min break ends the streak
        seg(H9 + 61 * 60, 15, "FOCUS", "winword.exe"),
        seg(H9 + 76 * 60, 5, "PAUSED"),
    ]
    out = daily_summary(segs, DAY)
    assert out["focus_minutes"] == 65.0
    assert out["longest_focus_streak_minutes"] == 50.0
    assert out["interruptions"] == 1
    assert out["seconds_per_state"]["READING"] == 600.0
    assert "PAUSED" not in out["seconds_per_state"]
    assert out["top_focus_apps"] == [("code.exe", 50.0), ("winword.exe", 15.0)]


def test_daily_summary_clips_at_midnight():
    late = T0 - 30 * 60  # 23:30 the night before
    out = daily_summary([seg(late, 60, "FOCUS")], DAY)
    assert out["focus_minutes"] == 30.0


def test_empty_day():
    out = daily_summary([], DAY)
    assert out["focus_minutes"] == 0 and out["longest_focus_streak_minutes"] == 0
    assert out["interruptions"] == 0 and out["top_focus_apps"] == []


# ─── Weekly view ────────────────────────────────────────────────────────────

def test_weekly_view_per_day_and_best_hour():
    segs = []
    for i in range(7):
        start = day_bounds(DAY - timedelta(days=6 - i))[0] + 14 * 3600  # 14:00 each day
        segs.append(seg(start, 10 * (i + 1), "FOCUS"))
    segs.append(seg(H9, 5, "FOCUS"))  # a little at 09:00 on the last day
    out = weekly_view(segs, DAY)
    assert [d["focus_minutes"] for d in out["days"]] == [10, 20, 30, 40, 50, 60, 75]
    assert out["days"][-1]["date"] == DAY.isoformat()
    assert out["best_focus_hour"] == 14


def test_weekly_view_splits_segment_across_hours():
    start = H9 + 50 * 60  # 09:50 to 10:30
    out = weekly_view([seg(start, 40, "FOCUS")], DAY, days=1)
    assert out["best_focus_hour"] == 10
    assert datetime.fromtimestamp(start).hour == 9
