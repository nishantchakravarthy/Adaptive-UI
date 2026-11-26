"""rules.json and the web dashboard must carry exactly the Python engine's rules."""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import export_rules  # noqa: E402


def test_rules_json_matches_engine():
    with open(os.path.join(ROOT, "rules.json"), encoding="utf-8") as f:
        assert json.load(f) == export_rules.build_rules(), "run: python tools/export_rules.py"


def test_web_dashboard_matches_engine():
    with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
        assert export_rules.web_block(f.read()) == export_rules.build_rules(), \
            "run: python tools/export_rules.py"


def test_rule_order_matches_engine_docstring():
    import state_engine
    order = export_rules.build_rules()["order"]
    assert set(order) == set(state_engine.StateEngine.VALID_STATES)
    doc = state_engine.__doc__
    positions = [doc.index(f". {s} ") for s in order]
    assert positions == sorted(positions)
