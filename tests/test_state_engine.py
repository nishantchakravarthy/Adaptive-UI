"""
Scripted metric streams fed through StateEngine with a fake clock.
TICK matches the real main loop (about 75 ms per cycle).
"""

import pytest

from state_engine import StateEngine, WINDOW_S, DEBOUNCE_S
from analyzer import compute_metrics

TICK = 0.075
SETTLE = WINDOW_S + DEBOUNCE_S + 0.5  # enough time for any steady stream to commit


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.idle = 0.0  # seconds since last input, carried across run() calls

    def __call__(self):
        return self.t


def make_engine(**kw):
    clock = FakeClock()
    return StateEngine(clock=clock, **kw), clock


def run(engine, clock, seconds, keys=0.0, clicks=0.0, move=0.0, scroll=0.0,
        idle=None, app=None, **flags):
    """Feed a steady stream; idle time grows while there is no input, else resets."""
    state = engine.current_state
    steps = int(round(seconds / TICK))
    if idle is not None:
        clock.idle = idle
    has_input = any((keys, clicks, move, scroll))
    for _ in range(steps):
        clock.t += TICK
        clock.idle = 0.0 if has_input else clock.idle + TICK
        idle_t = clock.idle
        state = engine.determine_state({
            "keys_per_sec": keys, "clicks_per_sec": clicks,
            "movement_per_sec": move, "scrolls_per_sec": scroll,
            "idle_time": idle_t, "interval": TICK, "foreground_app": app,
            **flags,
        })
    return state


# ─── Each state is reachable from a realistic stream ─────────────────────────

@pytest.mark.parametrize("stream, expected", [
    (dict(keys=5, move=10), "FOCUS"),
    (dict(scroll=1.5, move=40), "READING"),
    (dict(clicks=4, move=500), "ACTIVE"),
    (dict(keys=6, move=600), "ERROR"),
    (dict(keys=6, clicks=4, move=100), "ERROR"),
    (dict(keys=0.5, move=20), "LIGHT"),
])
def test_each_state_from_steady_stream(stream, expected):
    engine, clock = make_engine()
    assert run(engine, clock, SETTLE, **stream) == expected


def test_idle_after_quiet_period():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    assert engine.current_state == "FOCUS"
    assert run(engine, clock, 5.0 + DEBOUNCE_S + 0.5) == "IDLE"


def test_reading_pause_keeps_reading():
    engine, clock = make_engine()
    assert run(engine, clock, SETTLE, scroll=1.0, move=20) == "READING"
    assert run(engine, clock, 4.5) == "READING"       # reading without scrolling
    assert run(engine, clock, 2.0) == "IDLE"          # until truly idle


def test_thinking_pause_keeps_focus():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    assert run(engine, clock, 4.0) == "FOCUS"


def test_light_reached_after_active_slows_down():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, clicks=4, move=500)
    assert run(engine, clock, SETTLE, keys=0.5, move=30) == "LIGHT"


def test_heavy_mouse_while_typing_still_leaves_focus():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    assert run(engine, clock, SETTLE, keys=6, move=600) == "ERROR"


def test_short_pause_is_not_error_or_idle():
    """A 3 s pause after typing must never be reported as ERROR (the old catch-all)."""
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    seen = set()
    for _ in range(int(3.0 / TICK)):
        seen.add(run(engine, clock, TICK))
    assert "ERROR" not in seen and "IDLE" not in seen


# ─── Rule order ──────────────────────────────────────────────────────────────

def test_reading_wins_over_active_for_plain_scrolling():
    """Scroll 1.5/s exceeds ACTIVE's scroll rule but is still READING."""
    engine, _ = make_engine()
    avg = {"clicks_per_sec": 0, "keys_per_sec": 0, "movement_per_sec": 30, "scrolls_per_sec": 1.5}
    assert engine.classify(avg, 0) == "READING"


def test_error_wins_over_active_for_conflicting_input():
    engine, _ = make_engine()
    avg = {"clicks_per_sec": 0, "keys_per_sec": 6, "movement_per_sec": 600, "scrolls_per_sec": 0}
    assert engine.classify(avg, 0) == "ERROR"


def test_fast_scrolling_with_clicks_is_active():
    engine, _ = make_engine()
    avg = {"clicks_per_sec": 2, "keys_per_sec": 0, "movement_per_sec": 30, "scrolls_per_sec": 3}
    assert engine.classify(avg, 0) == "ACTIVE"


def test_idle_beats_everything():
    engine, _ = make_engine()
    avg = {"clicks_per_sec": 9, "keys_per_sec": 9, "movement_per_sec": 900, "scrolls_per_sec": 9}
    assert engine.classify(avg, 6.0) == "IDLE"


# ─── Debounce and holds ──────────────────────────────────────────────────────

def test_debounce_is_one_second():
    assert DEBOUNCE_S == 1.0 and WINDOW_S == 1.5


def test_mouse_nudge_while_typing_keeps_focus():
    engine, clock = make_engine(debounce_s=1.0)
    run(engine, clock, SETTLE, keys=5, move=10)
    before = len(engine.get_log())
    run(engine, clock, 0.25, keys=5, move=800)      # ~200 px nudge
    assert run(engine, clock, SETTLE, keys=5, move=10) == "FOCUS"
    assert len(engine.get_log()) == before          # no flicker at all


def test_new_state_needs_debounce_time():
    engine, clock = make_engine(debounce_s=1.0)
    run(engine, clock, SETTLE, keys=5, move=10)
    # 0.8 s of heavy clicking: the raw rule already says ACTIVE, debounce holds FOCUS
    assert run(engine, clock, 0.8, clicks=5, move=600) == "FOCUS"
    assert engine.classify(engine.averages, 0) == "ACTIVE"
    assert run(engine, clock, 1.5, clicks=5, move=600) == "ACTIVE"


def test_click_fast_path_is_immediate_and_held():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    assert run(engine, clock, TICK, instant_click=True) == "ACTIVE"
    assert run(engine, clock, 0.3) == "ACTIVE"        # still inside the 0.4 s hold


def test_typing_fast_path_is_immediate():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, scroll=1.5, move=30)
    assert engine.current_state == "READING"
    assert run(engine, clock, TICK, keys=4, instant_typing=True) == "FOCUS"


def test_typing_fast_path_ignored_while_moving_fast():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, clicks=4, move=600)
    assert run(engine, clock, TICK, keys=4, move=600, instant_typing=True) != "FOCUS"


# ─── Confidence and app bias ─────────────────────────────────────────────────

def test_app_bias_changes_confidence_not_state():
    plain, c1 = make_engine()
    biased, c2 = make_engine()
    s1 = run(plain, c1, SETTLE, keys=5, move=10, app="explorer.exe")
    s2 = run(biased, c2, SETTLE, keys=5, move=10, app="code.exe")
    assert s1 == s2 == "FOCUS"
    assert biased.confidence == pytest.approx(min(1.0, plain.confidence + 0.1))


def test_opposite_bias_lowers_confidence_but_keeps_state():
    engine, clock = make_engine()
    assert run(engine, clock, SETTLE, keys=5, move=10, app="chrome.exe") == "FOCUS"
    neutral, c2 = make_engine()
    run(neutral, c2, SETTLE, keys=5, move=10)
    assert engine.confidence == pytest.approx(neutral.confidence - 0.1)


def test_confidence_is_bounded():
    engine, clock = make_engine()
    for stream in (dict(keys=50, move=0), dict(clicks=50, move=5000), dict()):
        run(engine, clock, SETTLE, **stream)
        assert 0.0 <= engine.confidence <= 1.0


# ─── Thresholds and log ──────────────────────────────────────────────────────

def test_set_thresholds_changes_rules():
    engine, clock = make_engine()
    assert run(engine, clock, SETTLE, keys=1.5, move=10) == "LIGHT"
    engine.set_thresholds({"focus_keys": 1.0, "not_a_key": 9})
    assert run(engine, clock, SETTLE, keys=1.5, move=10) == "FOCUS"
    assert "not_a_key" not in engine.thresholds


def test_log_records_transitions():
    engine, clock = make_engine()
    run(engine, clock, SETTLE, keys=5, move=10)
    run(engine, clock, SETTLE, clicks=4, move=500)
    pairs = [(old, new) for _, old, new, _ in engine.get_log()]
    assert pairs[-2:] == [("IDLE", "FOCUS"), ("FOCUS", "ACTIVE")]


def test_every_detected_state_is_valid():
    assert set(StateEngine.VALID_STATES) == {"IDLE", "FOCUS", "READING", "ERROR", "ACTIVE", "LIGHT"}


# ─── Analyzer: rates use the measured interval ──────────────────────────────

def test_rates_use_measured_interval():
    m = compute_metrics(clicks=1, keys=3, move_distance=30, scroll_count=0,
                        last_event_time=0, interval=0.075)
    assert m["clicks_per_sec"] == pytest.approx(1 / 0.075)
    assert m["keys_per_sec"] == pytest.approx(3 / 0.075)
    assert m["movement_per_sec"] == pytest.approx(30 / 0.075)
    assert m["interval"] == pytest.approx(0.075)


def test_rates_fall_back_to_half_second():
    m = compute_metrics(clicks=1, keys=0, move_distance=0, scroll_count=0, last_event_time=0)
    assert m["clicks_per_sec"] == pytest.approx(2.0)
