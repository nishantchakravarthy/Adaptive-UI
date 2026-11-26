"""
main.py — Adaptive UI Engine Orchestrator (System Mode)
Runs as a background system tool with:
  - System tray icon
  - Global overlay windows
  - OS-level effects (brightness, notifications)
  - Dashboard accessible via tray menu

Pipeline: EventCapture → Analyzer → StateEngine → Overlays + OS Effects

Production Fixes:
  - Proper logging via logging module
  - Graceful shutdown: listeners → tray → overlays → root
  - Exception-safe update cycle with logger.exception()
  - Rates use the measured time between cycles (the loop runs about every 75 ms)
  - Thresholds and app bias come from config.json; first launch calibrates
    silently for two minutes (run with --recalibrate to repeat it)
"""

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import date
import customtkinter as ctk
from event_capture import EventCapture
from analyzer import compute_metrics
from state_engine import StateEngine
from ui_controller import AdaptiveUI
from overlay_manager import OverlayController
from tray_icon import TrayIcon
from system_bridge import apply_system_effects, reset_brightness, get_foreground_app, notify
from config import load_config, save_config, config_path, Calibrator
from storage import SessionStore
from insights import daily_summary, day_bounds
from focus_actions import BreakReminder, WindowsDnd, start_hotkey, hotkey_label
import oslayer

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AdaptiveUI.Main")


class AdaptiveEngine:
    """
    Main application controller.
    Runs the FSM loop and coordinates all subsystems.
    """

    FOREGROUND_POLL_S = 1.0

    def __init__(self, recalibrate=False):
        logger.info("Initializing Adaptive UI Engine...")

        # ─── Settings ───
        self.config = load_config()
        cal = self.config["calibration"]
        if recalibrate:
            cal["done"] = False
        self.calibrator = None
        if cal.get("enabled", True) and not cal.get("done", False):
            self.calibrator = Calibrator(cal.get("duration_seconds", 120))
            logger.info("Calibration running for %ss; use the computer normally.",
                        cal.get("duration_seconds", 120))

        # ─── Session history (Phase 2) ───
        self.store = None
        if self.config["history"].get("enabled", True):
            try:
                self.store = SessionStore(retention_days=self.config["history"].get("retention_days", 90))
            except Exception:
                logger.exception("Session history disabled: could not open the database")
        self._seg_state = None
        self._seg_start = time.time()

        # ─── Root Window (hidden by default) ───
        self.root = ctk.CTk()
        self.root.withdraw()  # Start hidden — background mode

        # ─── Output Layer: Dashboard (built but hidden) ───
        self.dashboard = AdaptiveUI(self.root)
        self.dashboard_visible = False
        self.dashboard.on_tray_toggle_callback = self._handle_tray_toggle

        # ─── Output Layer: Global Overlays ───
        self.overlays = OverlayController(self.root)

        # ─── Input Layer ───
        self.capture = EventCapture()

        # ─── Analysis Layer ───
        self.engine = StateEngine(
            thresholds=self.config["thresholds"],
            app_bias=self.config["app_bias"],
        )

        # ─── System Tray ───
        # ─── Focus actions (Phase 4) ───
        self._events = queue.Queue()  # hotkey + background results → Tk thread
        self.breaks = BreakReminder()
        self.dnd = WindowsDnd()
        self._dnd_active = False
        self._dnd_busy = False
        self._hotkey = None
        self._recover_dnd()

        self.tray = TrayIcon(flags={
            "breaks": self.config["focus"].get("break_reminders", True),
            "dnd": self.config["focus"].get("dnd", False),
            "dnd_available": self.dnd.available(),
            "autostart": oslayer.autostart_enabled(),
        })

        # ─── Track state for overlay decisions ───
        self._prev_state = None
        self._shutting_down = False
        self.is_paused = False
        self._last_cycle = time.monotonic()
        self._foreground_app = None
        self._foreground_checked = 0.0

        logger.info("All subsystems initialized.")

    def start(self):
        """Start all subsystems and enter main loop."""
        # Start input listeners
        self.capture.start()
        logger.info("Input listeners started.")

        # Start system tray icon
        self.tray.start()
        logger.info("System tray icon started.")

        # Global pause hotkey (events are handled on the Tk thread)
        self._hotkey = start_hotkey(lambda: self._events.put("pause"))
        if self._hotkey:
            logger.info("Pause hotkey: %s", hotkey_label())

        self._first_run_notice()

        # Start the 500ms update cycle
        self._update_cycle()

        # Handle window close → hide to tray (don't exit)
        self.root.protocol("WM_DELETE_WINDOW", self._hide_dashboard)

        logger.info("Entering main loop.")
        # Run tkinter mainloop
        self.root.mainloop()

    # ═══════════════════════════════════════════════════════════════════════════
    # 100ms UPDATE CYCLE
    # ═══════════════════════════════════════════════════════════════════════════

    def _update_cycle(self):
        """Core processing loop — runs every 100ms."""
        if self._shutting_down:
            return

        try:
            # 1. Get raw event data (clears buffer even if paused)
            now = time.monotonic()
            interval = now - self._last_cycle
            self._last_cycle = now
            clicks, keys, move_dist, scrolls, last_time = self.capture.get_and_reset()
            instant_click = self.capture.consume_instant_click()
            instant_typing = self.capture.consume_instant_typing()
            instant_scroll = self.capture.consume_instant_scroll()

            if self.is_paused:
                state = "PAUSED"
                metrics = {
                    "clicks_per_sec": 0.0,
                    "keys_per_sec": 0.0,
                    "movement_per_sec": 0.0,
                    "scrolls_per_sec": 0.0,
                    "idle_time": 0.0,
                    "neural_latency": 0,
                    "processing_cycle": 0,
                    "adaptation_response": 0,
                    "system_load": 0.0,
                    "throughput": 0,
                    "classification_confidence": 0.0,
                }
                state_duration = 0.0
                log_entries = self.engine.get_log()
            else:
                # 2. Compute metrics
                metrics = compute_metrics(clicks, keys, move_dist, scrolls, last_time,
                                         instant_click, instant_typing, instant_scroll,
                                         interval=interval)
                if now - self._foreground_checked >= self.FOREGROUND_POLL_S:
                    self._foreground_app = get_foreground_app()
                    self._foreground_checked = now
                metrics["foreground_app"] = self._foreground_app

                # 3. Determine FSM state
                state = self.engine.determine_state(metrics)
                metrics["classification_confidence"] = self.engine.confidence

                # 3b. First-run calibration samples the engine's smoothed rates
                if self.calibrator is not None:
                    sample = dict(self.engine.averages, idle_time=metrics["idle_time"])
                    self._feed_calibration(sample, interval)
                state_duration = self.engine.get_state_duration()
                log_entries = self.engine.get_log()

            # 3c. Session history: store the finished segment on every state change
            self._track_segment(state, metrics)

            # 3d. Tray menu, hotkey and background results
            self._drain_events()
            if self._shutting_down:
                return  # Exit was chosen; windows are gone

            # 3e. Focus actions: break reminder and experimental Do Not Disturb
            self._focus_actions(state, interval)

            # 4. Update tray icon state
            self.tray.update_state(state)

            # 5. Apply System-Level effects (brightness + notifications + mouse speed)
            if not self.is_paused:
                apply_system_effects(state, state_duration)

            # 6. Update UI based on mode
            if self.dashboard_visible:
                self.overlays.hide()
                self.dashboard.apply_state(state, metrics, state_duration, log_entries)
            else:
                if self.is_paused:
                    self.overlays.hide()
                else:
                    self.overlays.show_state(state, metrics)

        except Exception:
            logger.exception("Critical runtime failure in update cycle")
        finally:
            # 7. Schedule next cycle safely regardless of errors
            if not self._shutting_down:
                self.root.after(75, self._update_cycle)

    # ═══════════════════════════════════════════════════════════════════════════
    # EVENTS, SETTINGS AND FOCUS ACTIONS (Phase 4)
    # ═══════════════════════════════════════════════════════════════════════════

    def _save_config(self):
        try:
            save_config(self.config)
        except Exception:
            logger.exception("Could not save config.json")

    def _first_run_notice(self):
        parts = []
        if not self.config.get("first_run_notice_shown"):
            parts.append("Adaptive UI counts keystrokes, clicks, mouse movement and scrolling. "
                         "It never records what you type or which keys. "
                         f"Pause any time with {hotkey_label()}.")
            self.config["first_run_notice_shown"] = True
            self._save_config()
        warning = oslayer.session_warning()
        if warning:
            parts.append(warning)
            logger.warning(warning)
        if parts:
            notify("Adaptive UI Engine", " ".join(parts))

    def _drain_events(self):
        events = self.tray.poll_events()
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        for event in events:
            try:
                self._handle_event(event)
            except Exception:
                logger.exception("Failed to handle event %r", event)

    def _handle_event(self, event):
        if isinstance(event, tuple):  # background results
            kind, value = event
            if kind == "dnd_enabled":
                self._dnd_busy = False
                if value:
                    self._dnd_active = True
                    self.config["focus"]["dnd_restore"] = value
                    self._save_config()
            elif kind == "dnd_restored":
                self._dnd_busy = False
                if value:
                    self._dnd_active = False
                    self.config["focus"]["dnd_restore"] = None
                    self._save_config()
            return

        focus = self.config["focus"]
        if event == "open":
            self._do_toggle_dashboard()
        elif event == "pause":
            self._toggle_pause()
        elif event == "exit":
            self._do_exit_app()
        elif event == "summary":
            self._show_summary()
        elif event == "toggle_breaks":
            focus["break_reminders"] = not focus.get("break_reminders", True)
            self.breaks.reset()
            self.tray.set_flags(breaks=focus["break_reminders"])
            self._save_config()
        elif event == "toggle_dnd":
            focus["dnd"] = not focus.get("dnd", False)
            self.tray.set_flags(dnd=focus["dnd"])
            self._save_config()
            if not focus["dnd"]:
                self._dnd_restore_async()
        elif event == "toggle_autostart":
            wanted = not oslayer.autostart_enabled()
            if oslayer.set_autostart(wanted):
                self.tray.set_flags(autostart=wanted)
            else:
                notify("Adaptive UI Engine", "Could not change the start-at-login setting.")
        elif event == "recalibrate":
            self.calibrator = Calibrator(self.config["calibration"].get("duration_seconds", 120))
            notify("Adaptive UI Engine", "Recalibrating for two minutes. Keep working normally.")
        elif event == "open_config":
            self._save_config()
            self._open_path(config_path())

    @staticmethod
    def _open_path(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - opens the user's own settings file
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            logger.exception("Could not open %s", path)

    def _show_summary(self):
        if self.store is None:
            notify("Today", "Session history is turned off.")
            return
        self.store.flush()
        start, end = day_bounds(date.today())
        s = daily_summary(self.store.segments_between(start, end), date.today())
        apps = ", ".join(app for app, _ in s["top_focus_apps"]) or "none yet"
        notify("Today", f"Focus {s['focus_minutes']:.0f} min · longest streak "
                        f"{s['longest_focus_streak_minutes']:.0f} min · "
                        f"{s['interruptions']} interruptions · top apps: {apps}")

    def _focus_actions(self, state, interval):
        focus = self.config["focus"]
        live_state = "PAUSED" if self.is_paused else state
        if focus.get("break_reminders", True) and self.breaks.update(live_state, interval):
            notify("Time for a break", "You have been focused for 50 minutes.")

        if not (focus.get("dnd") and self.dnd.available()) or self._dnd_busy:
            return
        in_focus = live_state == "FOCUS" and self.engine.get_state_duration() >= 60
        left_focus = live_state != "FOCUS" and (self.breaks.away_s >= 120 or self.is_paused)
        if in_focus and not self._dnd_active:
            self._dnd_busy = True
            threading.Thread(target=lambda: self._events.put(("dnd_enabled", self.dnd.enable())),
                             daemon=True).start()
        elif left_focus and self._dnd_active:
            self._dnd_restore_async()

    def _dnd_restore_async(self):
        if not self._dnd_active or self._dnd_busy:
            return
        self._dnd_busy = True
        threading.Thread(target=lambda: self._events.put(("dnd_restored", self.dnd.restore())),
                         daemon=True).start()

    def _recover_dnd(self):
        """After a crash, put Do Not Disturb back the way the user had it."""
        saved = self.config["focus"].get("dnd_restore")
        if saved and self.dnd.available() and self.dnd.restore(original=saved):
            logger.info("Restored Do Not Disturb after an unclean exit.")
            self.config["focus"]["dnd_restore"] = None
            self._save_config()

    def _track_segment(self, state, metrics, closing=False):
        """Record the previous segment when the state changes (or on exit)."""
        now = time.time()
        if self._seg_state is None:
            self._seg_state, self._seg_start = state, now
            return
        if state == self._seg_state and not closing:
            return
        if self.store is not None:
            self.store.record(self._seg_start, now, self._seg_state,
                              None if closing else state,
                              metrics.get("classification_confidence"),
                              self._foreground_app)
        self._seg_state, self._seg_start = state, now

    def _feed_calibration(self, metrics, interval):
        """Add one sample; when the calibration window ends, apply and save."""
        self.calibrator.add(metrics, interval)
        if not self.calibrator.finished:
            return
        thresholds, report = self.calibrator.result(self.config["thresholds"])
        self.calibrator = None
        # Retry on next launch if there was too little input to calibrate
        self.config["calibration"]["done"] = report["used"]
        if report["used"]:
            self.config["thresholds"] = thresholds
            self.engine.set_thresholds(thresholds)
            logger.info("Calibration applied: %s", report["values"])
        else:
            logger.info("Calibration skipped (%.0fs of input); defaults kept.",
                        report["active_seconds"])
        try:
            save_config(self.config)
        except Exception:
            logger.exception("Could not save config.json")

    # ═══════════════════════════════════════════════════════════════════════════
    # DASHBOARD SHOW/HIDE & PAUSE
    # ═══════════════════════════════════════════════════════════════════════════

    def _toggle_pause(self):
        """Toggle the pause state of the system."""
        self.is_paused = not self.is_paused
        self.tray.set_paused(self.is_paused)
        if self.is_paused:
            logger.info("Monitoring paused by user.")
            reset_brightness()  # Restore brightness while paused
        else:
            logger.info("Monitoring resumed by user.")
            
    def _toggle_dashboard(self):
        """Toggle dashboard visibility (called from tray)."""
        self.root.after(0, self._do_toggle_dashboard)

    def _do_toggle_dashboard(self):
        if self.dashboard_visible:
            self._hide_dashboard()
        else:
            self._show_dashboard()

    def _show_dashboard(self):
        """Show the dashboard window and hide overlays."""
        self.overlays.hide()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.dashboard_visible = True

    def _hide_dashboard(self):
        """Hide dashboard back to tray, resume overlays."""
        self.root.withdraw()
        self.dashboard_visible = False

    def _handle_tray_toggle(self, is_enabled):
        """Handle toggle switch for the system tray icon."""
        if is_enabled:
            self.tray.start()
        else:
            self.tray.stop()

    # ═══════════════════════════════════════════════════════════════════════════
    # EXIT — Graceful Shutdown
    # ═══════════════════════════════════════════════════════════════════════════

    def _exit_app(self):
        """Clean shutdown — stop everything."""
        self.root.after(0, self._do_exit_app)

    def _do_exit_app(self):
        """Graceful shutdown sequence: listeners → tray → overlays → root."""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutting down Adaptive UI Engine...")

        try:
            reset_brightness()
            from system_bridge import restore_mouse_speed
            restore_mouse_speed()
        except Exception:
            logger.exception("Failed to reset system effects")

        try:
            if self._dnd_active and self.dnd.restore():
                self.config["focus"]["dnd_restore"] = None
                self._save_config()
            if self._hotkey is not None:
                self._hotkey.stop()
        except Exception:
            logger.exception("Failed to undo focus actions")

        try:
            if self.store is not None:
                self._track_segment(self._seg_state, {}, closing=True)
                self.store.close()
                logger.info("Session history saved.")
        except Exception:
            logger.exception("Failed to close session history")

        try:
            self.capture.stop()
            logger.info("Input listeners stopped.")
        except Exception:
            logger.exception("Failed to stop input listeners")

        try:
            self.tray.stop()
            logger.info("Tray icon stopped.")
        except Exception:
            logger.exception("Failed to stop tray icon")

        try:
            self.overlays.destroy()
            logger.info("Overlays destroyed.")
        except Exception:
            logger.exception("Failed to destroy overlays")

        try:
            self.root.quit()
            self.root.destroy()
            logger.info("Root window destroyed. Goodbye.")
        except Exception:
            logger.exception("Failed to destroy root window")


def main():
    engine = AdaptiveEngine(recalibrate="--recalibrate" in sys.argv[1:])
    engine.start()


if __name__ == "__main__":
    main()
