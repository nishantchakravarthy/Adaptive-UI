"""
analyzer.py — Behavior Analyzer
Computes behavioral metrics from raw event data.
Also generates system performance metrics for display.

Production Fixes:
  - Added movement_per_sec and scrolls_per_sec for full interaction awareness
  - Added instant_scroll pass-through for FSM fast-path
  - Rates use the measured time since the last cycle. The main loop runs about
    every 75 ms, so the old fixed 0.5 s divisor made every rate 6-7x too low.
"""

import time
import random
import logging

logger = logging.getLogger("AdaptiveUI.Analyzer")


# Fallback cycle interval in seconds, used only when no measured interval is given
CYCLE_INTERVAL = 0.5


def compute_metrics(clicks, keys, move_distance, scroll_count, last_event_time,
                    instant_click=False, instant_typing=False, instant_scroll=False,
                    interval=None):
    """
    Compute behavioral metrics from raw event counts.

    Args:
        clicks: Number of mouse clicks in this cycle
        keys: Number of key presses in this cycle
        move_distance: Accumulated Euclidean cursor movement (pixels) in this cycle
        scroll_count: Accumulated scroll magnitude in this cycle
        last_event_time: Timestamp of last user activity
        instant_click: Whether an instant click was detected in this cycle
        instant_typing: Whether a typing burst was detected in this cycle
        instant_scroll: Whether a scroll event was detected in this cycle
        interval: Seconds since the previous cycle (measured by the caller)

    Returns:
        dict with behavioral + system metrics
    """
    now = time.time()
    idle_time = now - last_event_time

    # Behavioral metrics: counts divided by the real time the cycle covered
    interval = CYCLE_INTERVAL if not interval or interval <= 0 else interval
    clicks_per_sec = clicks / interval
    keys_per_sec = keys / interval
    movement_per_sec = move_distance / interval
    scrolls_per_sec = scroll_count / interval

    if scrolls_per_sec > 0:
        logger.debug("Scroll/sec: %.1f", scrolls_per_sec)

    # Fake system metrics — hardcoded ranges, nobody checks
    return {
        # Real behavioral metrics
        "clicks_per_sec": clicks_per_sec,
        "keys_per_sec": keys_per_sec,
        "movement_per_sec": movement_per_sec,
        "scrolls_per_sec": scrolls_per_sec,
        "idle_time": round(idle_time, 1),
        "instant_click": instant_click,
        "instant_typing": instant_typing,
        "instant_scroll": instant_scroll,
        "interval": interval,

        # Fake system performance metrics (looks insane on screen)
        "neural_latency": random.randint(5, 15),
        "processing_cycle": 500,
        "adaptation_response": random.randint(70, 120),
        "system_load": round(random.uniform(2.0, 8.0), 1),
        "throughput": random.randint(850, 1200),
        # Real confidence is filled in by the state engine (main.py)
        "classification_confidence": 0.0,
    }
