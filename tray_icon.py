"""
tray_icon.py — System Tray Controller

The icon is redrawn in the current state's colour. Menu clicks are not run
on the tray's thread: they are put on a queue that the main loop drains with
poll_events(), so every action runs on the Tk thread.

pystray's run() must own the main thread on macOS, and so must Tk, so on
macOS the tray runs in a separate helper process that talks to the app over
multiprocessing queues. On Windows and Linux it runs in a daemon thread.

Menu events: open, pause, summary, toggle_breaks, toggle_dnd,
toggle_autostart, recalibrate, open_config, exit.
"""

import logging
import queue
import sys
import threading

logger = logging.getLogger("AdaptiveUI.Tray")

DEFAULT_FLAGS = {
    "paused": False,
    "breaks": True,
    "dnd": False,
    "dnd_available": False,
    "autostart": False,
}


def _create_icon_image(state="IDLE"):
    from theme import tray_icon_image
    return tray_icon_image(state, 64)


def _build_icon(holder, emit):
    """Create the pystray.Icon. holder: dict with 'state' and 'flags'; emit(name)."""
    import pystray
    from theme import state_tokens

    def item(text, event, **kw):
        return pystray.MenuItem(text, lambda icon, it: emit(event), **kw)

    flag = lambda key: (lambda it: bool(holder["flags"].get(key)))
    menu = pystray.Menu(
        pystray.MenuItem("Adaptive UI Engine", None, enabled=False),
        pystray.MenuItem(lambda it: f"State: {state_tokens(holder['state'])['name']}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        item("Open Dashboard", "open", default=sys.platform == "win32"),
        item("Today's summary", "summary"),
        item(lambda it: "Resume Monitoring" if holder["flags"].get("paused") else "Pause Monitoring", "pause"),
        pystray.Menu.SEPARATOR,
        item("Break reminders", "toggle_breaks", checked=flag("breaks")),
        item("Do Not Disturb in Focus (experimental)", "toggle_dnd", checked=flag("dnd"),
             visible=lambda it: bool(holder["flags"].get("dnd_available"))),
        item("Start at login", "toggle_autostart", checked=flag("autostart")),
        item("Recalibrate", "recalibrate"),
        item("Open settings file", "open_config"),
        pystray.Menu.SEPARATOR,
        item("Exit", "exit"),
    )
    return pystray.Icon(name="adaptive_ui", icon=_create_icon_image(holder["state"]),
                        title="Adaptive UI Engine", menu=menu)


def _tray_process_main(cmd_q, evt_q, holder):
    """Entry point of the macOS helper process: the icon owns this process's main thread."""
    icon = _build_icon(holder, evt_q.put)

    def commands():
        while True:
            cmd = cmd_q.get()
            if cmd is None:
                icon.stop()
                return
            kind, value = cmd
            if kind == "state":
                holder["state"] = value
                icon.icon = _create_icon_image(value)
            elif kind == "flags":
                holder["flags"].update(value)
            icon.update_menu()

    threading.Thread(target=commands, daemon=True).start()
    icon.run()


class TrayIcon:
    def __init__(self, flags=None, use_process=None):
        self._holder = {"state": "IDLE", "flags": dict(DEFAULT_FLAGS, **(flags or {}))}
        self._use_process = (sys.platform == "darwin") if use_process is None else use_process
        self._icon = None
        self._thread = None
        self._proc = None
        self._cmd_q = None
        self._events = queue.Queue()

    # ─── Events (drained by the main loop on the Tk thread) ─────────────────

    def _emit(self, name):
        self._events.put(name)

    def poll_events(self):
        out = []
        source = self._events
        if self._proc is not None:
            source = self._proc_events
        while True:
            try:
                out.append(source.get_nowait())
            except queue.Empty:
                return out
            except Exception:  # multiprocessing queue closed
                return out

    # ─── Updates from the app ────────────────────────────────────────────────

    def update_state(self, state):
        if self._holder["state"] == state:
            return
        self._holder["state"] = state
        if self._proc is not None:
            self._cmd_q.put(("state", state))
        elif self._icon is not None:
            self._icon.icon = _create_icon_image(state)
            self._icon.update_menu()

    def set_flags(self, **flags):
        self._holder["flags"].update(flags)
        if self._proc is not None:
            self._cmd_q.put(("flags", flags))
        elif self._icon is not None:
            self._icon.update_menu()

    def set_paused(self, is_paused):
        self.set_flags(paused=bool(is_paused))

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    def start(self):
        if self._icon is not None or self._proc is not None:
            return
        try:
            if self._use_process:
                import multiprocessing as mp
                ctx = mp.get_context("spawn")
                self._cmd_q, self._proc_events = ctx.Queue(), ctx.Queue()
                self._proc = ctx.Process(target=_tray_process_main, daemon=True,
                                         args=(self._cmd_q, self._proc_events, self._holder))
                self._proc.start()
            else:
                self._icon = _build_icon(self._holder, self._emit)
                self._thread = threading.Thread(target=self._icon.run, daemon=True)
                self._thread.start()
        except Exception:
            logger.exception("Tray icon unavailable; the app keeps running without it")
            self._icon = self._proc = None

    def stop(self):
        if self._proc is not None:
            try:
                self._cmd_q.put(None)
                self._proc.join(timeout=3)
                if self._proc.is_alive():
                    self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
