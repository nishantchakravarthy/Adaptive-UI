"""
config.py — Engine thresholds, foreground-app bias map and per-user calibration.

Settings live in config.json in the per-user app-data folder:
  Windows: %APPDATA%/AdaptiveUI/config.json
  macOS:   ~/Library/Application Support/AdaptiveUI/config.json
  Linux:   ~/.config/AdaptiveUI/config.json

Calibration never moves a threshold below half or above double its default,
and is ignored if fewer than MIN_CALIBRATION_ACTIVE_S seconds of input were seen.
"""

import copy
import json
import logging
import os
import sys
import tempfile

logger = logging.getLogger("AdaptiveUI.Config")

# ─── Default thresholds (rates are per second, averaged over WINDOW_S) ────────
DEFAULT_THRESHOLDS = {
    "idle_seconds": 5.0,          # IDLE when no input for longer than this
    "focus_keys": 2.0,            # FOCUS: keys/s above this ...
    "focus_move_max": 50.0,       # ... with movement below this (px/s)
    "focus_scroll_max": 2.0,      # ... and scroll below this
    "reading_scroll": 0.3,        # READING: scroll/s above this ...
    "reading_keys_max": 1.0,      # ... keys/s below this
    "reading_clicks_max": 1.0,    # ... clicks/s below this
    "reading_move_max": 200.0,    # ... movement below this (px/s)
    "error_keys": 4.0,            # ERROR: keys/s above this together with ...
    "error_clicks": 3.0,          # ... clicks/s above this, or
    "error_move": 400.0,          # ... movement above this (px/s)
    "active_clicks": 3.0,         # ACTIVE: clicks/s above this, or
    "active_move": 200.0,         # ... movement above this (px/s), or
    "active_scroll": 1.0,         # ... scroll/s above this
}

# Thresholds that calibration may adjust: (metric, percentile, floor).
# Samples are the engine's 1.5 s smoothed averages; values at or below the floor
# (stray single keys, sensor drift) are ignored. focus_keys uses the 25th
# percentile so that about three quarters of the user's typing clears it.
CALIBRATED_KEYS = {
    "focus_keys": ("keys_per_sec", 25, 0.5),
    "active_move": ("movement_per_sec", 50, 10.0),
}

# Process name (lower case) → state the app leans towards. Bias only nudges
# confidence; it never changes which state is chosen.
DEFAULT_APP_BIAS = {
    "code.exe": "FOCUS",
    "pycharm64.exe": "FOCUS",
    "idea64.exe": "FOCUS",
    "devenv.exe": "FOCUS",
    "sublime_text.exe": "FOCUS",
    "notepad.exe": "FOCUS",
    "notepad++.exe": "FOCUS",
    "winword.exe": "FOCUS",
    "obsidian.exe": "FOCUS",
    "windowsterminal.exe": "FOCUS",
    "chrome.exe": "READING",
    "msedge.exe": "READING",
    "firefox.exe": "READING",
    "brave.exe": "READING",
    "acrord32.exe": "READING",
    "acrobat.exe": "READING",
    "sumatrapdf.exe": "READING",
}

DEFAULTS = {
    "version": 1,
    "thresholds": dict(DEFAULT_THRESHOLDS),
    "app_bias": dict(DEFAULT_APP_BIAS),
    "calibration": {
        "enabled": True,
        "done": False,
        "duration_seconds": 120,
    },
    "history": {
        "enabled": True,
        "retention_days": 90,
    },
    "focus": {
        "break_reminders": True,
        "dnd": False,             # experimental, Windows only
        "dnd_restore": None,      # profile to restore after a crash
    },
    "first_run_notice_shown": False,
}

MIN_CALIBRATION_ACTIVE_S = 60.0


def config_dir():
    """Per-user folder for AdaptiveUI data."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "AdaptiveUI")


def config_path():
    return os.path.join(config_dir(), "config.json")


def _merge(defaults, loaded):
    """Recursively fill missing keys from defaults; ignore unknown types."""
    out = copy.deepcopy(defaults)
    if not isinstance(loaded, dict):
        return out
    for key, value in loaded.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value) if key != "app_bias" else {
                str(k).lower(): str(v).upper() for k, v in value.items()
            }
        else:
            out[key] = value
    return out


def load_config(path=None):
    """Load config.json merged over defaults. A missing or broken file gives defaults."""
    path = path or config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _merge(DEFAULTS, json.load(f))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError):
        logger.warning("Could not read %s; using defaults.", path)
        return copy.deepcopy(DEFAULTS)


def save_config(cfg, path=None):
    """Write config atomically so a crash never leaves a half-written file."""
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _percentile(values, pct):
    """Linear-interpolated percentile of a non-empty list."""
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def compute_calibrated_thresholds(samples, defaults=None):
    """
    Derive thresholds from calibration samples.

    samples: list of dicts with "interval" (seconds) and the per-second rates
             "keys_per_sec", "movement_per_sec", "idle_time".
    Returns (thresholds, report). Thresholds equal the defaults when there is
    too little input; every calibrated value is clamped to [0.5x, 2x] default.
    """
    defaults = dict(defaults or DEFAULT_THRESHOLDS)
    active = [s for s in samples if s.get("idle_time", 0) <= defaults["idle_seconds"]]
    active_seconds = sum(s.get("interval", 0) for s in active)
    report = {"active_seconds": round(active_seconds, 1), "used": False, "values": {}}

    if active_seconds < MIN_CALIBRATION_ACTIVE_S:
        return defaults, report

    result = dict(defaults)
    for key, (metric, pct, floor) in CALIBRATED_KEYS.items():
        values = [s[metric] for s in active if s.get(metric, 0) > floor]
        if len(values) < 10:
            continue
        # Clamp against the shipped default, so repeated calibration cannot drift
        default = DEFAULT_THRESHOLDS[key]
        value = _percentile(values, pct)
        result[key] = round(min(max(value, default * 0.5), default * 2.0), 2)
        report["values"][key] = result[key]

    report["used"] = bool(report["values"])
    return result, report


class Calibrator:
    """Collects metric samples for a fixed wall-clock duration, then finishes."""

    def __init__(self, duration_seconds=120):
        self.duration = float(duration_seconds)
        self.elapsed = 0.0
        self.samples = []

    @property
    def finished(self):
        return self.elapsed >= self.duration

    def add(self, metrics, interval):
        """metrics should hold smoothed rates (StateEngine.averages) plus idle_time."""
        if self.finished:
            return
        self.elapsed += interval
        self.samples.append({
            "interval": interval,
            "keys_per_sec": metrics.get("keys_per_sec", 0.0),
            "movement_per_sec": metrics.get("movement_per_sec", 0.0),
            "idle_time": metrics.get("idle_time", 0.0),
        })

    def result(self, defaults=None):
        return compute_calibrated_thresholds(self.samples, defaults)
