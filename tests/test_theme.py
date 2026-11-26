import re

import pytest
from PIL import Image

import theme
from state_engine import StateEngine


def test_every_state_has_tokens():
    for state in StateEngine.VALID_STATES + ("PAUSED",):
        tokens = theme.STATES[state]
        for key in ("field", "glow", "accent", "dot"):
            assert re.fullmatch(r"#[0-9a-f]{6}", tokens[key]), (state, key)


def _lum(hex_color):
    r, g, b = (c / 255 for c in theme.hex_to_rgb(hex_color))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


@pytest.mark.parametrize("state", list(theme.STATES))
def test_text_contrast_on_every_field_is_at_least_4_5(state):
    a, b = _lum(theme.INK), _lum(theme.STATES[state]["field"])
    ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
    assert ratio >= 4.5, (state, round(ratio, 2))


def test_render_field_size_and_colour():
    img = theme.render_field("FOCUS", (320, 200))
    assert img.size == (320, 200) and img.mode == "RGB"
    # Mostly blue: the average pixel leans towards blue
    r, g, b = img.resize((1, 1), resample=Image.Resampling.BOX).getpixel((0, 0))
    assert b > r and b > g


def test_crossfade_ends_on_target():
    a = theme.render_field("FOCUS", (64, 40))
    b = theme.render_field("ERROR", (64, 40))
    frames = theme.crossfade_frames(a, b)
    assert len(frames) == theme.FADE_FRAMES == 10
    assert frames[-1].tobytes() == b.tobytes()


def test_unknown_state_falls_back():
    assert theme.state_tokens("NOPE") is theme.STATES["LIGHT"]


def test_tray_icon_uses_state_colour():
    img = theme.tray_icon_image("ACTIVE", 64)
    assert img.size == (64, 64)
    assert img.getpixel((32, 32))[:3] == theme.hex_to_rgb(theme.STATES["ACTIVE"]["dot"])
