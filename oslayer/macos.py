"""
macOS implementation (pyobjc for AppKit; ctypes for permission checks).

pynput only receives global input once the app is allowed under
Privacy & Security -> Accessibility (and Input Monitoring for the keyboard on
recent versions). permissions_ok() checks both so the first-run notice can
point the user to the right pane.
"""

import ctypes
import ctypes.util
import os
import plistlib

NAME = "macos"
_LABEL = "com.adaptiveui.engine"


def _launch_agent_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{_LABEL}.plist")


def get_cursor_pos():
    from AppKit import NSEvent  # pyobjc
    p = NSEvent.mouseLocation()
    return int(p.x), int(p.y)


def get_foreground_app():
    from AppKit import NSWorkspace  # pyobjc
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    url = app.executableURL()
    name = url.lastPathComponent() if url is not None else app.localizedName()
    return str(name).lower() if name else None


def make_click_through(window):
    """
    Tk windows map to NSWindows; match by the overlay's unique title and set
    ignoresMouseEvents. Overlays must set a unique title before calling this.
    """
    from AppKit import NSApplication  # pyobjc
    title = window.title()
    for ns_window in NSApplication.sharedApplication().windows():
        if str(ns_window.title()) == title:
            ns_window.setIgnoresMouseEvents_(True)
            return True
    return False


def autostart_enabled():
    return os.path.exists(_launch_agent_path())


def set_autostart(enabled, command):
    path = _launch_agent_path()
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump({"Label": _LABEL, "ProgramArguments": list(command),
                       "RunAtLoad": True, "ProcessType": "Interactive"}, f)
    return True


def session_warning():
    if not permissions_ok():
        return ("Allow Adaptive UI under System Settings > Privacy & Security > "
                "Accessibility and Input Monitoring, then restart it.")
    return None


def permissions_ok():
    """AXIsProcessTrusted (Accessibility) and IOHIDCheckAccess (Input Monitoring)."""
    ax = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    trusted = ax.AXIsProcessTrusted()

    listen_ok = True
    iokit_path = ctypes.util.find_library("IOKit")
    if iokit_path:
        iokit = ctypes.cdll.LoadLibrary(iokit_path)
        if hasattr(iokit, "IOHIDCheckAccess"):
            iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
            # kIOHIDRequestTypeListenEvent = 1; result 0 = granted
            listen_ok = iokit.IOHIDCheckAccess(1) == 0
    return bool(trusted and listen_ok)
