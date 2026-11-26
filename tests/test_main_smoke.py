"""
Runs the real AdaptiveEngine update loop with the GUI, input hooks and tray
replaced by stand-ins, to check the wiring between phases end to end.
"""

import sys
import types
from datetime import date

import pytest


class _Widget:
    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeRoot(_Widget):
    def __init__(self):
        self.scheduled = []

    def after(self, ms, fn=None, *args):
        if fn is not None:
            self.scheduled.append(fn)


class FakeCapture:
    def __init__(self):
        self.next = (0, 0, 0.0, 0.0)
        self.last = 0.0
        self.instant = {"click": False, "typing": False, "scroll": False}

    def start(self):
        pass

    def stop(self):
        pass

    def get_and_reset(self):
        c, k, m, s = self.next
        return c, k, m, s, self.last

    def consume_instant_click(self):
        return False

    def consume_instant_typing(self):
        return False

    def consume_instant_scroll(self):
        return False


class FakeUI(_Widget):
    def __init__(self, root):
        self.states = []

    def apply_state(self, state, *a):
        self.states.append(state)


class FakeOverlays(_Widget):
    def __init__(self, root):
        self.shown = []

    def show_state(self, state, metrics=None):
        self.shown.append(state)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ctk = types.ModuleType("customtkinter")
    ctk.CTk = FakeRoot
    monkeypatch.setitem(sys.modules, "customtkinter", ctk)
    for name, attr, obj in (("event_capture", "EventCapture", FakeCapture),
                            ("ui_controller", "AdaptiveUI", FakeUI),
                            ("overlay_manager", "OverlayController", FakeOverlays)):
        mod = types.ModuleType(name)
        setattr(mod, attr, obj)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    import main
    notes = []
    monkeypatch.setattr(main, "notify", lambda t, m: notes.append((t, m)))
    monkeypatch.setattr(main, "get_foreground_app", lambda: "code.exe")
    monkeypatch.setattr(main, "apply_system_effects", lambda *a: None)
    monkeypatch.setattr(main, "reset_brightness", lambda: None)
    clock = {"t": 10_000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["t"])
    engine = main.AdaptiveEngine()
    engine._notes, engine._clock = notes, clock
    engine.engine._clock = lambda: clock["t"]
    yield engine, main
    if engine.store:
        engine.store.close()


def cycles(engine, seconds, counts, idle=False):
    """Advance 75 ms cycles with the given per-cycle counts (clicks, keys, move, scroll)."""
    for _ in range(int(seconds / 0.075)):
        engine._clock["t"] += 0.075
        engine.capture.next = counts
        if not idle:
            engine.capture.last = __import__("time").time()
        engine._update_cycle()


def test_loop_detects_states_records_history_and_handles_tray(app):
    engine, main = app
    engine.start = None  # never enter a real mainloop
    engine._first_run_notice()
    assert "never records what you type" in engine._notes[0][1]

    cycles(engine, 4, (0, 1, 0.0, 0.0))              # ~13 keys/s, still mouse
    assert engine.engine.current_state == "FOCUS"
    assert engine.overlays.shown[-1] == "FOCUS"
    assert 0 < engine.engine.confidence <= 1

    cycles(engine, 4, (0, 0, 1.0, 0.1))              # scrolling, a little movement
    assert engine.engine.current_state == "READING"

    engine.tray._emit("summary")
    cycles(engine, 0.15, (0, 0, 1.0, 0.1))
    assert any(t == "Today" for t, _ in engine._notes)

    engine.tray._emit("toggle_breaks")
    cycles(engine, 0.15, (0, 0, 1.0, 0.1))
    assert engine.config["focus"]["break_reminders"] is False

    engine._events.put("pause")                       # the global hotkey path
    cycles(engine, 0.3, (0, 0, 0.0, 0.0))
    assert engine.is_paused and engine._seg_state == "PAUSED"

    engine.store.flush()
    start, end = main.day_bounds(date.today())
    states = [s["state"] for s in engine.store.segments_between(start - 86400, end + 86400)]
    assert "FOCUS" in states and "READING" in states
    seg = [s for s in engine.store.segments_between(0, 2e10) if s["state"] == "FOCUS"][0]
    assert seg["app"] == "code.exe"

    engine._events.put("exit")
    cycles(engine, 0.15, (0, 0, 0.0, 0.0))
    assert engine._shutting_down
