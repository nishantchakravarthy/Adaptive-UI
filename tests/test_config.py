import json

import pytest

import config
from config import (DEFAULT_THRESHOLDS, Calibrator, compute_calibrated_thresholds,
                    load_config, save_config)


def samples(seconds, keys=0.0, move=0.0, idle=0.0, tick=0.075):
    return [{"interval": tick, "keys_per_sec": keys, "movement_per_sec": move, "idle_time": idle}
            for _ in range(int(seconds / tick))]


def test_too_little_input_keeps_defaults():
    th, report = compute_calibrated_thresholds(samples(30, keys=5, move=100))
    assert th == DEFAULT_THRESHOLDS
    assert report["used"] is False


def test_idle_time_does_not_count_as_input():
    th, report = compute_calibrated_thresholds(samples(120, keys=5, idle=10))
    assert report["active_seconds"] == 0
    assert th == DEFAULT_THRESHOLDS


def test_calibration_tracks_slow_typist():
    data = samples(90, keys=1.5, move=60)
    th, report = compute_calibrated_thresholds(data)
    assert report["used"]
    assert th["focus_keys"] == pytest.approx(1.5)
    assert th["active_move"] == pytest.approx(DEFAULT_THRESHOLDS["active_move"] * 0.5)  # clamped


def test_calibration_is_clamped_to_double():
    th, _ = compute_calibrated_thresholds(samples(90, keys=40, move=5000))
    assert th["focus_keys"] == DEFAULT_THRESHOLDS["focus_keys"] * 2
    assert th["active_move"] == DEFAULT_THRESHOLDS["active_move"] * 2


def test_clamp_uses_shipped_defaults_not_previous_calibration():
    previous = dict(DEFAULT_THRESHOLDS, focus_keys=4.0)
    th, _ = compute_calibrated_thresholds(samples(90, keys=40, move=300), previous)
    assert th["focus_keys"] == DEFAULT_THRESHOLDS["focus_keys"] * 2


def test_only_calibrated_keys_change():
    th, _ = compute_calibrated_thresholds(samples(90, keys=3, move=250))
    changed = {k for k in th if th[k] != DEFAULT_THRESHOLDS[k]}
    assert changed <= set(config.CALIBRATED_KEYS)


def test_calibrator_stops_after_duration():
    cal = Calibrator(duration_seconds=1.0)
    for _ in range(40):
        cal.add({"keys_per_sec": 3, "movement_per_sec": 20, "idle_time": 0}, 0.075)
    assert cal.finished
    assert len(cal.samples) == 14  # 14 x 75 ms reaches 1.0 s


def test_missing_file_gives_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "nope.json"))
    assert cfg["thresholds"] == DEFAULT_THRESHOLDS
    assert cfg["calibration"]["done"] is False


def test_broken_file_gives_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    assert load_config(str(p))["thresholds"] == DEFAULT_THRESHOLDS


def test_round_trip_and_partial_file(tmp_path):
    p = tmp_path / "sub" / "config.json"
    cfg = load_config(str(p))
    cfg["thresholds"]["focus_keys"] = 1.7
    save_config(cfg, str(p))
    again = load_config(str(p))
    assert again["thresholds"]["focus_keys"] == 1.7
    # A file missing keys is filled from defaults
    p.write_text(json.dumps({"thresholds": {"focus_keys": 3}}))
    merged = load_config(str(p))
    assert merged["thresholds"]["focus_keys"] == 3
    assert merged["thresholds"]["active_move"] == DEFAULT_THRESHOLDS["active_move"]


def test_user_app_list_replaces_defaults_and_is_normalised(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"app_bias": {"Notion.EXE": "focus"}}))
    assert load_config(str(p))["app_bias"] == {"notion.exe": "FOCUS"}
