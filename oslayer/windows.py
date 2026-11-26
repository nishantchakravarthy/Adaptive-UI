"""Windows implementation (ctypes + winreg only; no pywin32)."""

import ctypes
import os

NAME = "windows"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "AdaptiveUI"


def get_cursor_pos():
    from ctypes import wintypes
    pt = wintypes.POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return None


def get_foreground_app():
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value).lower() or None
    finally:
        kernel32.CloseHandle(handle)


def make_click_through(window):
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(window.winfo_id())
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
    return True


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled, command):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            value = " ".join(f'"{part}"' for part in command)
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
            except FileNotFoundError:
                pass
    return True


def session_warning():
    return None


def permissions_ok():
    return True
