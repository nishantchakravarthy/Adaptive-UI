"""
Linux implementation for X11 sessions (python-xlib).

Under Wayland, pynput only receives input from apps running through Xwayland,
so native Wayland apps are invisible to the engine; session_warning() says so.
"""

import os

NAME = "linux"
_DESKTOP_NAME = "adaptive-ui.desktop"
_display = None


def _x():
    global _display
    if _display is None:
        from Xlib import display  # python-xlib
        _display = display.Display()
    return _display


def is_wayland(env=None):
    env = os.environ if env is None else env
    return bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def session_warning(env=None):
    if is_wayland(env):
        return ("Wayland session detected: input from native Wayland apps is not visible to "
                "Adaptive UI. Log in to an X11 session for full accuracy.")
    return None


def get_cursor_pos():
    root = _x().screen().root
    p = root.query_pointer()
    return p.root_x, p.root_y


def get_foreground_app():
    d = _x()
    root = d.screen().root
    active = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), 0)
    if not active or not active.value or not active.value[0]:
        return None
    win = d.create_resource_object("window", active.value[0])
    pid_prop = win.get_full_property(d.intern_atom("_NET_WM_PID"), 0)
    if not pid_prop or not pid_prop.value:
        return None
    return process_name(int(pid_prop.value[0]))


def process_name(pid, proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, str(pid), "comm"), "r", encoding="utf-8") as f:
            return f.read().strip().lower() or None
    except OSError:
        return None


def make_click_through(window):
    """Give the overlay's top-level X window an empty input shape."""
    from Xlib.ext import shape
    d = _x()
    xid = int(window.wm_frame(), 16)
    win = d.create_resource_object("window", xid)
    win.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
    d.flush()
    return True


def _autostart_path(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart", _DESKTOP_NAME)


def autostart_enabled(env=None):
    return os.path.exists(_autostart_path(env))


def _quote(arg):
    # Desktop Entry spec: quote arguments containing spaces or reserved characters
    if any(c in arg for c in ' \t"\'\\$`'):
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'
    return arg


def set_autostart(enabled, command, env=None):
    path = _autostart_path(env)
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Adaptive UI Engine\n"
                f"Exec={' '.join(_quote(a) for a in command)}\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=false\n")
    return True


def permissions_ok():
    return True
