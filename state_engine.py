"""
state_engine.py — Cognitive State Engine (FSM)

Classifies user behaviour into six detected states (PAUSED is set by the user
in main.py, never detected here).

Rule order, checked top to bottom on a time-weighted average over WINDOW_S;
the first match wins:
    1. IDLE     — idle time above idle_seconds
    2. FOCUS    — steady typing, still cursor, little scrolling
    3. READING  — scrolling with little typing, clicking or movement
    4. ERROR    — heavy typing together with heavy clicking or movement
    5. ACTIVE   — many clicks, fast movement or fast scrolling
    6. LIGHT    — some input that matches none of the above

READING and ERROR must sit above ACTIVE: ACTIVE's rule is broad and would
otherwise always match first, so neither could ever occur.

LIGHT never interrupts FOCUS or READING: pausing to think while typing, or
reading without scrolling, keeps that state until another rule (or IDLE)
takes over. LIGHT is reached from IDLE, ACTIVE or ERROR.

A new state takes over only after it has won continuously for DEBOUNCE_S.
Two fast-paths keep the UI responsive: a click enters ACTIVE at once (held
0.4 s), and a typing burst with a still cursor enters FOCUS at once (held 0.6 s).

Confidence (0–1) measures how far past its thresholds the input is. The
foreground app's bias nudges confidence by up to 0.1 but never changes the state.
"""

import time
import logging
from collections import deque

from config import DEFAULT_THRESHOLDS, DEFAULT_APP_BIAS

logger = logging.getLogger("AdaptiveUI.FSM")

WINDOW_S = 1.5       # moving-average window, in seconds
DEBOUNCE_S = 1.0     # how long a new state must keep winning before it is committed
CLICK_HOLD_S = 0.4
TYPING_HOLD_S = 0.6
LOG_LIMIT = 100


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


class StateEngine:
    """Behaviour classification FSM."""

    VALID_STATES = ("IDLE", "FOCUS", "READING", "ERROR", "ACTIVE", "LIGHT")

    def __init__(self, thresholds=None, app_bias=None, window_s=WINDOW_S,
                 debounce_s=DEBOUNCE_S, clock=time.time):
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        self.app_bias = {k.lower(): v.upper() for k, v in (app_bias or DEFAULT_APP_BIAS).items()}
        self.window_s = window_s
        self.debounce_s = debounce_s
        self._clock = clock

        now = clock()
        self.current_state = "IDLE"
        self.state_start_time = now
        self.log = []  # (timestamp_str, old_state, new_state, duration)

        self._samples = deque()  # (time, interval, clicks/s, keys/s, move/s, scroll/s)
        self.candidate_state = "IDLE"
        self.candidate_since = now
        self.active_hold_until = 0.0
        self.focus_hold_until = 0.0

        self.confidence = 0.0
        self.averages = {"clicks_per_sec": 0.0, "keys_per_sec": 0.0,
                         "movement_per_sec": 0.0, "scrolls_per_sec": 0.0}

    # ─── Configuration ────────────────────────────────────────────────────────

    def set_thresholds(self, thresholds):
        """Replace thresholds (e.g. after calibration); unknown keys are ignored."""
        for key, value in thresholds.items():
            if key in self.thresholds:
                self.thresholds[key] = float(value)

    # ─── Core ─────────────────────────────────────────────────────────────────

    def _average(self, now):
        """Time-weighted average of each rate over the last window_s seconds."""
        while len(self._samples) > 1 and self._samples[0][0] < now - self.window_s:
            self._samples.popleft()
        total = sum(s[1] for s in self._samples) or 1.0
        keys = ("clicks_per_sec", "keys_per_sec", "movement_per_sec", "scrolls_per_sec")
        return {k: sum(s[1] * s[2 + i] for s in self._samples) / total for i, k in enumerate(keys)}

    def classify(self, avg, idle_time):
        """Pure rule check, in priority order. No debounce or holds."""
        t = self.thresholds
        clicks = avg["clicks_per_sec"]
        keys = avg["keys_per_sec"]
        move = avg["movement_per_sec"]
        scroll = avg["scrolls_per_sec"]

        if idle_time > t["idle_seconds"]:
            return "IDLE"
        if keys > t["focus_keys"] and move < t["focus_move_max"] and scroll < t["focus_scroll_max"]:
            return "FOCUS"
        if (scroll > t["reading_scroll"] and keys < t["reading_keys_max"]
                and clicks < t["reading_clicks_max"] and move < t["reading_move_max"]):
            return "READING"
        if keys > t["error_keys"] and (clicks > t["error_clicks"] or move > t["error_move"]):
            return "ERROR"
        if clicks > t["active_clicks"] or move > t["active_move"] or scroll > t["active_scroll"]:
            return "ACTIVE"
        return "LIGHT"

    def _confidence(self, state, avg, idle_time, app):
        t = self.thresholds
        clicks, keys = avg["clicks_per_sec"], avg["keys_per_sec"]
        move, scroll = avg["movement_per_sec"], avg["scrolls_per_sec"]

        if state == "IDLE":
            base = 0.6 + (idle_time - t["idle_seconds"]) / 10.0
        elif state == "FOCUS":
            base = (0.5 + 0.25 * _clip((keys - t["focus_keys"]) / t["focus_keys"])
                    + 0.25 * _clip(1 - move / t["focus_move_max"]))
        elif state == "READING":
            base = 0.5 + 0.5 * _clip((scroll - t["reading_scroll"]) / (t["reading_scroll"] * 3))
        elif state == "ACTIVE":
            over = max(clicks / t["active_clicks"], move / t["active_move"],
                       scroll / t["active_scroll"]) - 1
            base = 0.5 + 0.5 * _clip(over)
        elif state == "ERROR":
            base = 0.4
        else:  # LIGHT
            base = 0.3

        bias = self.app_bias.get((app or "").lower())
        if bias == state:
            base += 0.1
        elif bias and state in ("FOCUS", "READING") and bias in ("FOCUS", "READING"):
            base -= 0.1
        return round(_clip(base), 2)

    def _commit(self, state, now):
        prev = self.current_state
        if state == prev:
            return
        duration = round(now - self.state_start_time, 1)
        self.log.append((time.strftime("%H:%M:%S"), prev, state, duration))
        if len(self.log) > LOG_LIMIT:
            self.log = self.log[-LOG_LIMIT:]
        self.current_state = state
        self.state_start_time = now
        logger.debug("%s -> %s after %.1fs", prev, state, duration)

    def determine_state(self, metrics, now=None):
        """
        Classify one cycle of metrics and return the committed state.

        metrics keys: clicks_per_sec, keys_per_sec, movement_per_sec,
        scrolls_per_sec, idle_time, interval (seconds this sample covers),
        optional instant_click, instant_typing, foreground_app.
        """
        now = self._clock() if now is None else now
        interval = max(float(metrics.get("interval", 0.5)), 1e-3)
        idle_time = float(metrics.get("idle_time", 0.0))
        app = metrics.get("foreground_app")

        self._samples.append((now, interval,
                              float(metrics.get("clicks_per_sec", 0.0)),
                              float(metrics.get("keys_per_sec", 0.0)),
                              float(metrics.get("movement_per_sec", 0.0)),
                              float(metrics.get("scrolls_per_sec", 0.0))))
        avg = self._average(now)
        self.averages = avg

        # Fast-path: a click means ACTIVE immediately.
        if metrics.get("instant_click", False):
            self.active_hold_until = now + CLICK_HOLD_S
            self.candidate_state, self.candidate_since = "ACTIVE", now
            self._commit("ACTIVE", now)
            self.confidence = self._confidence("ACTIVE", avg, idle_time, app)
            return self.current_state

        # Fast-path: a typing burst with a still cursor means FOCUS immediately.
        if (metrics.get("instant_typing", False) and now >= self.active_hold_until
                and avg["movement_per_sec"] < self.thresholds["focus_move_max"]
                and float(metrics.get("clicks_per_sec", 0.0)) <= 0):
            self.focus_hold_until = now + TYPING_HOLD_S
            self.candidate_state, self.candidate_since = "FOCUS", now
            self._commit("FOCUS", now)
            self.confidence = self._confidence("FOCUS", avg, idle_time, app)
            return self.current_state

        # Rules, then debounce: a new state must keep winning for debounce_s.
        raw = self.classify(avg, idle_time)
        if raw == "LIGHT" and self.current_state in ("FOCUS", "READING"):
            raw = self.current_state  # pauses and small nudges keep the state
        if raw != self.candidate_state:
            self.candidate_state, self.candidate_since = raw, now
        state = self.current_state
        if raw != self.current_state and now - self.candidate_since >= self.debounce_s:
            state = raw

        # Minimum holds after a fast-path.
        if self.current_state == "ACTIVE" and now < self.active_hold_until:
            state = "ACTIVE"
        if self.current_state == "FOCUS" and now < self.focus_hold_until:
            state = "FOCUS"

        self._commit(state, now)
        self.confidence = self._confidence(self.current_state, avg, idle_time, app)
        return self.current_state

    def get_state_duration(self):
        """How long the system has been in the current state."""
        return round(self._clock() - self.state_start_time, 1)

    def get_log(self, n=50):
        """Return the last n transition entries."""
        return self.log[-n:]
