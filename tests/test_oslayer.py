import sys

import pytest

import oslayer
from oslayer import linux


def test_selected_backend_matches_platform():
    expected = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    assert oslayer.NAME == expected


def test_wrapper_never_raises_without_a_display(monkeypatch):
    monkeypatch.setattr(oslayer._impl, "get_cursor_pos", lambda: 1 / 0)
    monkeypatch.setattr(oslayer._impl, "get_foreground_app", lambda: 1 / 0)
    assert oslayer.get_cursor_pos() is None
    assert oslayer.get_foreground_app() is None


def test_launch_command_points_at_main():
    cmd = oslayer.launch_command()
    assert cmd[0] == sys.executable and cmd[-1].endswith("main.py")


@pytest.mark.parametrize("env, wayland", [
    ({"WAYLAND_DISPLAY": "wayland-0"}, True),
    ({"XDG_SESSION_TYPE": "wayland"}, True),
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, False),
    ({}, False),
])
def test_wayland_detection(env, wayland):
    assert linux.is_wayland(env) is wayland
    assert (linux.session_warning(env) is not None) is wayland


def test_linux_autostart_round_trip(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    cmd = ["/usr/bin/python3", "/home/me/My Apps/adaptive-ui/main.py"]
    assert not linux.autostart_enabled(env)
    assert linux.set_autostart(True, cmd, env)
    path = tmp_path / "autostart" / "adaptive-ui.desktop"
    text = path.read_text()
    assert "Exec=/usr/bin/python3 \"/home/me/My Apps/adaptive-ui/main.py\"" in text
    assert text.startswith("[Desktop Entry]")
    assert linux.autostart_enabled(env)
    assert linux.set_autostart(False, cmd, env) and not path.exists()


def test_linux_process_name_from_proc(tmp_path):
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "comm").write_text("Code\n")
    assert linux.process_name(42, proc_root=str(tmp_path)) == "code"
    assert linux.process_name(43, proc_root=str(tmp_path)) is None


def test_macos_launch_agent(tmp_path, monkeypatch):
    import plistlib
    from oslayer import macos
    monkeypatch.setattr(macos, "_launch_agent_path", lambda: str(tmp_path / "a.plist"))
    assert macos.set_autostart(True, ["/Applications/Adaptive UI.app/Contents/MacOS/AdaptiveUI"])
    data = plistlib.loads((tmp_path / "a.plist").read_bytes())
    assert data["RunAtLoad"] is True and data["Label"] == "com.adaptiveui.engine"
    assert macos.autostart_enabled()
    assert macos.set_autostart(False, []) and not macos.autostart_enabled()


def test_modules_import_without_optional_dependencies():
    # windows/macos import only stdlib at module level; deps load inside functions
    import importlib
    for name in ("oslayer.windows", "oslayer.macos", "oslayer.linux"):
        importlib.import_module(name)
