"""
Export the engine's rules to rules.json and into web/index.html.

state_engine.py and config.py are the source of truth. This script writes the
same values to rules.json and to the <script id="engine-rules"> block of the web
dashboard; tests/test_rules_sync.py fails if either drifts.

    python tools/export_rules.py
"""

import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from config import DEFAULT_THRESHOLDS  # noqa: E402
import state_engine as se  # noqa: E402

RULES_PATH = os.path.join(ROOT, "rules.json")
WEB_PATH = os.path.join(ROOT, "web", "index.html")
BLOCK = re.compile(r'(<script id="engine-rules" type="application/json">)(.*?)(</script>)', re.S)


def build_rules():
    return {
        "version": 1,
        "order": ["IDLE", "FOCUS", "READING", "ERROR", "ACTIVE", "LIGHT"],
        "light_never_interrupts": ["FOCUS", "READING"],
        "window_seconds": se.WINDOW_S,
        "debounce_seconds": se.DEBOUNCE_S,
        "click_hold_seconds": se.CLICK_HOLD_S,
        "typing_hold_seconds": se.TYPING_HOLD_S,
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }


def rules_text():
    return json.dumps(build_rules(), indent=2) + "\n"


def web_block(html):
    m = BLOCK.search(html)
    return json.loads(m.group(2)) if m else None


def main():
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        f.write(rules_text())
    with open(WEB_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    compact = json.dumps(build_rules(), separators=(",", ":"))
    if not BLOCK.search(html):
        raise SystemExit('web/index.html has no <script id="engine-rules" type="application/json"> block')
    html = BLOCK.sub(lambda m: m.group(1) + compact + m.group(3), html)
    with open(WEB_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("Wrote rules.json and updated web/index.html")


if __name__ == "__main__":
    main()
