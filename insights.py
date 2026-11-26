"""
insights.py — Turns stored segments into daily and weekly summaries.

Definitions
  focus streak   a run of FOCUS time in which every break is shorter than
                 INTERRUPTION_MAX_S; its length counts FOCUS time only
  interruption   one such short break (under two minutes of other activity)
                 between two FOCUS segments
  top apps       foreground apps ranked by FOCUS time
PAUSED time is excluded everywhere. All days are local calendar days.
"""

from collections import defaultdict
from datetime import datetime, timedelta

INTERRUPTION_MAX_S = 120.0
COUNTED_STATES = ("FOCUS", "READING", "ACTIVE", "LIGHT", "ERROR", "IDLE")


def day_bounds(day):
    """(start, end) timestamps of a local calendar day; day is a date."""
    start = datetime(day.year, day.month, day.day)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _clip(segments, start, end):
    out = []
    for s in segments:
        a, b = max(s["started"], start), min(s["ended"], end)
        if b > a:
            out.append(dict(s, started=a, ended=b))
    return sorted(out, key=lambda s: s["started"])


def _focus_streaks(segments):
    """Return (streak lengths in seconds, interruption count)."""
    streaks, interruptions = [], 0
    current, last_focus_end = 0.0, None
    for s in segments:
        if s["state"] != "FOCUS":
            continue
        length = s["ended"] - s["started"]
        if last_focus_end is not None and s["started"] - last_focus_end < INTERRUPTION_MAX_S:
            if s["started"] > last_focus_end:
                interruptions += 1
            current += length
        else:
            if current:
                streaks.append(current)
            current = length
        last_focus_end = s["ended"]
    if current:
        streaks.append(current)
    return streaks, interruptions


def daily_summary(segments, day):
    """Summary for one local day from segments (any range; clipped here)."""
    start, end = day_bounds(day)
    segs = [s for s in _clip(segments, start, end) if s["state"] != "PAUSED"]

    per_state = {state: 0.0 for state in COUNTED_STATES}
    focus_by_app = defaultdict(float)
    for s in segs:
        length = s["ended"] - s["started"]
        per_state[s["state"]] = per_state.get(s["state"], 0.0) + length
        if s["state"] == "FOCUS" and s.get("app"):
            focus_by_app[s["app"]] += length

    streaks, interruptions = _focus_streaks(segs)
    top_apps = sorted(focus_by_app.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "date": day.isoformat(),
        "seconds_per_state": {k: round(v, 1) for k, v in per_state.items()},
        "focus_minutes": round(per_state["FOCUS"] / 60.0, 1),
        "longest_focus_streak_minutes": round(max(streaks, default=0.0) / 60.0, 1),
        "interruptions": interruptions,
        "top_focus_apps": [(app, round(sec / 60.0, 1)) for app, sec in top_apps],
    }


def weekly_view(segments, last_day, days=7):
    """Focus minutes for each of the last `days` days and the best hour of day."""
    first = last_day - timedelta(days=days - 1)
    per_day = []
    by_hour = defaultdict(float)
    for i in range(days):
        day = first + timedelta(days=i)
        start, end = day_bounds(day)
        focus = 0.0
        for s in _clip(segments, start, end):
            if s["state"] != "FOCUS":
                continue
            focus += s["ended"] - s["started"]
            # spread the segment across the clock hours it covers
            t = s["started"]
            while t < s["ended"]:
                hour_start = datetime.fromtimestamp(t).replace(minute=0, second=0, microsecond=0)
                hour_end = (hour_start + timedelta(hours=1)).timestamp()
                chunk_end = min(hour_end, s["ended"])
                by_hour[hour_start.hour] += chunk_end - t
                t = chunk_end
        per_day.append({"date": day.isoformat(), "focus_minutes": round(focus / 60.0, 1)})
    best_hour = max(by_hour.items(), key=lambda kv: kv[1])[0] if by_hour else None
    return {"days": per_day, "best_focus_hour": best_hour}
