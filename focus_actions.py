"""
focus_actions.py — What the app does when it detects focus (Phase 4).

BreakReminder (all platforms)
    Fires once after BREAK_AFTER_S of continuous FOCUS. Breaks shorter than
    INTERRUPTION_MAX_S do not reset the count (same rule as insights.py).

WindowsDnd (Windows only, experimental, off by default)
    Windows has no documented API to change Do Not Disturb; public APIs can
    only read it. This uses the undocumented QuietHoursSettings COM server
    (CLSID F53321FA-..., IQuietHoursSettings vtable slots 3 = get and 4 = put
    UserSelectedProfile), which Microsoft can change in any build. So it:
      * saves the user's current profile before switching (and to config, for
        crash recovery),
      * reads the profile back after every switch (Windows applies it
        asynchronously) and reports failure,
      * restores the original when focus ends or the app exits,
      * reports unavailable if any call fails, and the tray hides the option.
"""

import ctypes
import logging
import sys
import threading
import time
import uuid

logger = logging.getLogger("AdaptiveUI.Focus")

BREAK_AFTER_S = 50 * 60
INTERRUPTION_MAX_S = 120.0


class BreakReminder:
    def __init__(self, after_s=BREAK_AFTER_S, grace_s=INTERRUPTION_MAX_S):
        self.after_s = after_s
        self.grace_s = grace_s
        self.focus_s = 0.0
        self.away_s = 0.0
        self.fired = False

    def update(self, state, dt):
        """Feed every cycle; returns True exactly once when a break is due."""
        if state == "FOCUS":
            self.focus_s += dt
            self.away_s = 0.0
        else:
            self.away_s += dt
            if self.away_s >= self.grace_s:
                self.focus_s, self.fired = 0.0, False
        if not self.fired and self.focus_s >= self.after_s:
            self.fired = True
            return True
        return False

    def reset(self):
        self.focus_s, self.away_s, self.fired = 0.0, 0.0, False


# ─── Windows Do Not Disturb (experimental) ───────────────────────────────────

PROFILE_OFF = "Microsoft.QuietHoursProfile.Unrestricted"
PROFILE_PRIORITY = "Microsoft.QuietHoursProfile.PriorityOnly"
PROFILE_ALARMS = "Microsoft.QuietHoursProfile.AlarmsOnly"

_CLSID = "{F53321FA-34F8-4B7F-B9A3-361877CB94CF}"
_IID = "{6BFF4732-81EC-4FFB-AE67-B6C1BC29631F}"
_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, text):
        u = uuid.UUID(text)
        g = cls(u.time_low, u.time_mid, u.time_hi_version)
        g.Data4[:] = list(u.bytes[8:])
        return g


def _run_on_sta_thread(fn):
    """Run fn on a fresh COM STA thread (never the Tk thread); return its result."""
    box = {}

    def target():
        ole32 = ctypes.windll.ole32
        hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        try:
            box["result"] = fn()
        except Exception as e:
            box["error"] = e
        finally:
            if hr >= 0:
                ole32.CoUninitialize()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=5)
    if "error" in box:
        raise box["error"]
    if "result" not in box:
        raise TimeoutError("COM call timed out")
    return box["result"]


def _with_settings(action):
    """Create IQuietHoursSettings, call action(obj, vtbl), release it."""
    ole32 = ctypes.windll.ole32
    obj = ctypes.c_void_p()
    clsid, iid = _GUID.from_string(_CLSID), _GUID.from_string(_IID)
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_ALL,
                                ctypes.byref(iid), ctypes.byref(obj))
    if hr < 0 or not obj.value:
        raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    try:
        return action(obj, vtbl)
    finally:
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        release(obj)


def _get_profile_raw():
    def action(obj, vtbl):
        out = ctypes.c_void_p()
        get = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p))(vtbl[3])
        hr = get(obj, ctypes.byref(out))
        if hr < 0 or not out.value:
            raise OSError(f"get_UserSelectedProfile failed: 0x{hr & 0xFFFFFFFF:08X}")
        try:
            return ctypes.wstring_at(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    return _with_settings(action)


def _set_profile_raw(profile):
    def action(obj, vtbl):
        put = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p)(vtbl[4])
        hr = put(obj, profile)
        if hr < 0:
            raise OSError(f"put_UserSelectedProfile failed: 0x{hr & 0xFFFFFFFF:08X}")
        return True
    return _with_settings(action)


class WindowsDnd:
    """Switches Do Not Disturb on during focus and always restores the original."""

    def __init__(self, getter=None, setter=None, platform=sys.platform, settle_s=0.1, tries=10):
        self._get = getter or (lambda: _run_on_sta_thread(_get_profile_raw))
        self._set = setter or (lambda p: _run_on_sta_thread(lambda: _set_profile_raw(p)))
        self._platform = platform
        self._settle_s, self._tries = settle_s, tries
        self.original = None     # profile to restore; None = we changed nothing
        self._available = None

    def available(self):
        if self._available is None:
            if self._platform != "win32":
                self._available = False
            else:
                try:
                    self._get()
                    self._available = True
                except Exception as e:
                    logger.info("Do Not Disturb control unavailable: %s", e)
                    self._available = False
        return self._available

    def _switch(self, profile):
        self._set(profile)
        for _ in range(self._tries):  # Windows applies the change asynchronously
            if self._get() == profile:
                return True
            time.sleep(self._settle_s)
        return False

    def enable(self):
        """Turn on Priority only if it is off. Returns the profile to restore, or None."""
        if not self.available() or self.original is not None:
            return self.original
        try:
            current = self._get()
            if current != PROFILE_OFF:
                return None  # user already has DND on: leave it alone
            if self._switch(PROFILE_PRIORITY):
                self.original = current
                return current
            logger.warning("Do Not Disturb switch not confirmed; restoring")
            self._set(current)
        except Exception:
            logger.exception("Do Not Disturb switch failed; disabling the option")
            self._available = False
        return None

    def restore(self, original=None):
        """Put back the saved profile (or `original` after a crash). True when done."""
        target = original or self.original
        if not target:
            return True
        try:
            current = self._get()
            # Only restore if the profile is still the one we set: a change the
            # user made since is left alone.
            if current == PROFILE_PRIORITY and current != target:
                if not self._switch(target):
                    return False
            self.original = None
            return True
        except Exception:
            logger.exception("Could not restore Do Not Disturb")
            return False


# ─── Global hotkey ───────────────────────────────────────────────────────────

def hotkey_combo(platform=sys.platform):
    """Pause/resume shortcut in pynput syntax."""
    return "<cmd>+<alt>+p" if platform == "darwin" else "<ctrl>+<alt>+p"


def hotkey_label(platform=sys.platform):
    return "Cmd+Option+P" if platform == "darwin" else "Ctrl+Alt+P"


def start_hotkey(callback):
    """Register the global pause hotkey; returns the listener (or None on failure)."""
    try:
        from pynput import keyboard
        listener = keyboard.GlobalHotKeys({hotkey_combo(): callback})
        listener.daemon = True
        listener.start()
        return listener
    except Exception:
        logger.exception("Global hotkey unavailable")
        return None
