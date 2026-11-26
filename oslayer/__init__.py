"""
oslayer — one interface over Windows, macOS and Linux (X11).

Named oslayer, not platform, so it never shadows Python's built-in `platform`
module. Every function degrades to a safe default (None / False) instead of
raising, so a missing optional dependency never crashes the engine.

    get_cursor_pos()            -> (x, y) or None
    get_foreground_app()        -> lower-case process name or None
    make_click_through(window)  -> True if the overlay now passes clicks through
    autostart_enabled()         -> bool
    set_autostart(enabled)      -> bool (success)
    session_warning()           -> str or None (e.g. Wayland limitations)
    permissions_ok()            -> bool (macOS Accessibility / Input Monitoring)
"""

import os
import sys

if sys.platform == "win32":
    from . import windows as _impl
elif sys.platform == "darwin":
    from . import macos as _impl
else:
    from . import linux as _impl

NAME = _impl.NAME


def launch_command():
    """Command that starts the app: the frozen executable, or python + main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return [sys.executable, main_py]


def get_cursor_pos():
    try:
        return _impl.get_cursor_pos()
    except Exception:
        return None


def get_foreground_app():
    try:
        return _impl.get_foreground_app()
    except Exception:
        return None


def make_click_through(window):
    try:
        return bool(_impl.make_click_through(window))
    except Exception:
        return False


def autostart_enabled():
    try:
        return bool(_impl.autostart_enabled())
    except Exception:
        return False


def set_autostart(enabled):
    try:
        return bool(_impl.set_autostart(bool(enabled), launch_command()))
    except Exception:
        return False


def session_warning():
    try:
        return _impl.session_warning()
    except Exception:
        return None


def permissions_ok():
    try:
        return bool(_impl.permissions_ok())
    except Exception:
        return True
