import pytest

from focus_actions import (BreakReminder, WindowsDnd, PROFILE_OFF, PROFILE_PRIORITY,
                           PROFILE_ALARMS, hotkey_combo, hotkey_label, _GUID)


# ─── Break reminder ─────────────────────────────────────────────────────────

def feed(r, state, seconds, dt=0.5):
    fired = 0
    for _ in range(int(seconds / dt)):
        fired += r.update(state, dt)
    return fired


def test_fires_once_after_50_minutes_of_focus():
    r = BreakReminder()
    assert feed(r, "FOCUS", 49 * 60) == 0
    assert feed(r, "FOCUS", 61) == 1
    assert feed(r, "FOCUS", 600) == 0  # only once per streak


def test_short_breaks_do_not_reset():
    r = BreakReminder()
    for _ in range(5):
        feed(r, "FOCUS", 10 * 60)
        feed(r, "ACTIVE", 60)  # 1-minute breaks
    assert r.fired


def test_long_break_resets_and_rearms():
    r = BreakReminder()
    feed(r, "FOCUS", 50 * 60 + 1)
    feed(r, "IDLE", 5 * 60)
    assert r.focus_s == 0 and not r.fired
    assert feed(r, "FOCUS", 50 * 60 + 1) == 1


# ─── Do Not Disturb with a fake Windows ─────────────────────────────────────

class FakeWin:
    def __init__(self, profile=PROFILE_OFF, lag=0, fail=False):
        self.profile, self.pending, self.lag, self.fail = profile, None, lag, fail

    def get(self):
        if self.fail:
            raise OSError("no COM")
        if self.pending and self.lag <= 0:
            self.profile, self.pending = self.pending, None
        self.lag -= 1
        return self.profile

    def set(self, p):
        self.pending = p


def dnd(win, platform="win32"):
    return WindowsDnd(getter=win.get, setter=win.set, platform=platform, settle_s=0, tries=10)


def test_enable_and_restore_round_trip():
    win = FakeWin()
    d = dnd(win)
    assert d.enable() == PROFILE_OFF
    assert win.get() == PROFILE_PRIORITY
    assert d.restore() is True
    assert win.get() == PROFILE_OFF and d.original is None


def test_waits_for_asynchronous_switch():
    win = FakeWin(lag=3)
    assert dnd(win).enable() == PROFILE_OFF
    assert win.get() == PROFILE_PRIORITY


def test_unconfirmed_switch_is_reverted():
    win = FakeWin(lag=999)
    d = dnd(win)
    assert d.enable() is None and d.original is None


def test_existing_user_dnd_is_left_alone():
    win = FakeWin(profile=PROFILE_ALARMS)
    d = dnd(win)
    assert d.enable() is None
    assert d.restore() is True
    assert win.get() == PROFILE_ALARMS


def test_user_change_during_focus_is_not_overwritten():
    win = FakeWin()
    d = dnd(win)
    d.enable()
    win.profile = PROFILE_ALARMS  # user picked Alarms only meanwhile
    assert d.restore() is True
    assert win.get() == PROFILE_ALARMS


def test_crash_recovery_restores_saved_original():
    win = FakeWin(profile=PROFILE_PRIORITY)
    assert dnd(win).restore(original=PROFILE_OFF) is True
    assert win.get() == PROFILE_OFF


def test_unavailable_off_windows_or_on_failure():
    assert dnd(FakeWin(), platform="linux").available() is False
    assert dnd(FakeWin(fail=True)).available() is False
    assert dnd(FakeWin(fail=True)).enable() is None


def test_guid_parsing_matches_clsid_layout():
    g = _GUID.from_string("{F53321FA-34F8-4B7F-B9A3-361877CB94CF}")
    assert (g.Data1, g.Data2, g.Data3) == (0xF53321FA, 0x34F8, 0x4B7F)
    assert bytes(g.Data4) == bytes.fromhex("B9A3361877CB94CF")


# ─── Hotkey ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("plat, combo, label", [
    ("win32", "<ctrl>+<alt>+p", "Ctrl+Alt+P"),
    ("linux", "<ctrl>+<alt>+p", "Ctrl+Alt+P"),
    ("darwin", "<cmd>+<alt>+p", "Cmd+Option+P"),
])
def test_hotkey_per_platform(plat, combo, label):
    assert hotkey_combo(plat) == combo and hotkey_label(plat) == label
