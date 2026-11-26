"""
theme.py — Design tokens for the v2 desktop redesign (Phase 3).

Single source of truth for colours, type and spacing. UI files should read
from here instead of hard-coding hex values. Colours match the web dashboard
(web/index.html): each state is a deep colour field with one accent.

Also renders the per-state colour-field backgrounds with Pillow. Tk has no
per-widget transparency, so a crossfade is a short list of frames pre-blended
with Image.blend and swapped on a label every FRAME_MS with after().
"""

from PIL import Image, ImageDraw, ImageFilter

# ─── State tokens ────────────────────────────────────────────────────────────
# field: page background; glow: soft light inside the field; accent: the one
# highlight colour (also the tray icon); dot: small state marker on dark UI.
STATES = {
    "ACTIVE":  {"name": "Active",         "field": "#0b4a3b", "glow": "#1fa37a", "accent": "#8ff2c9", "dot": "#3ecf8e"},
    "FOCUS":   {"name": "Focus",          "field": "#1a2786", "glow": "#4a5cff", "accent": "#c3ccff", "dot": "#7b93ff"},
    "READING": {"name": "Reading",        "field": "#123a5c", "glow": "#2f7fbf", "accent": "#b9dcff", "dot": "#5aa9ef"},
    "LIGHT":   {"name": "Light activity", "field": "#2a2d33", "glow": "#565c68", "accent": "#e1e5ec", "dot": "#a3abb8"},
    "IDLE":    {"name": "Idle",           "field": "#2b2840", "glow": "#6a5f9e", "accent": "#d8d2f2", "dot": "#a99ee0"},
    "ERROR":   {"name": "Error",          "field": "#5e1620", "glow": "#b8323f", "accent": "#ffc2c6", "dot": "#f2646a"},
    "PAUSED":  {"name": "Paused",         "field": "#26272b", "glow": "#4b4e57", "accent": "#d6d7da", "dot": "#8d9099"},
}

INK = "#f5f3ef"           # main text on every field
INK_MUTED = "#c8c5bf"     # secondary text
PANEL = "#141518"         # statistics drawer background
PANEL_LINE = "#25272c"

FONT_SANS = "Inter Tight"        # bundled in assets/fonts (OFL 1.1)
FONT_SERIF = "Instrument Serif"  # italic state name (OFL 1.1)
FONT_FALLBACK = "Segoe UI"

TYPE = {"state": 96, "title": 22, "body": 15, "small": 12}  # px
SPACE = 8  # spacing grid unit (px)
RADIUS = 14

FADE_MS = 400
FRAME_MS = 40
FADE_FRAMES = FADE_MS // FRAME_MS  # 10


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def state_tokens(state):
    """Tokens for a state; unknown states fall back to LIGHT."""
    return STATES.get(state, STATES["LIGHT"])


def render_field(state, size):
    """
    One background image for a state: the deep field colour with two soft glows
    and darker edges. size = (width, height). Returns an RGB PIL image.
    """
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    tokens = state_tokens(state)
    base = Image.new("RGB", (w, h), hex_to_rgb(tokens["field"]))

    # Glows are drawn small then upscaled + blurred: cheap and smooth.
    small = (max(1, w // 8), max(1, h // 8))
    glow = Image.new("L", small, 0)
    d = ImageDraw.Draw(glow)
    sw, sh = small
    d.ellipse([sw * 0.05, sh * 0.05, sw * 0.6, sh * 0.7], fill=150)
    d.ellipse([sw * 0.5, sh * 0.45, sw * 1.0, sh * 1.0], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, min(sw, sh) // 5))).resize((w, h))
    lit = Image.new("RGB", (w, h), hex_to_rgb(tokens["glow"]))
    base = Image.composite(lit, base, glow.point(lambda v: int(v * 0.55)))

    # Vignette
    vig = Image.new("L", small, 0)
    ImageDraw.Draw(vig).ellipse([-sw * 0.2, -sh * 0.2, sw * 1.2, sh * 1.2], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(max(1, min(sw, sh) // 4))).resize((w, h))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(base, dark, vig.point(lambda v: int(150 + v * 105 / 255)))


def crossfade_frames(from_img, to_img, frames=FADE_FRAMES):
    """Pre-blended frames from one field to another; the last frame is to_img."""
    if from_img.size != to_img.size:
        from_img = from_img.resize(to_img.size)
    return [Image.blend(from_img, to_img, (i + 1) / frames) for i in range(frames)]


def tray_icon_image(state, size=64):
    """Round tray icon filled with the state's accent dot colour."""
    tokens = state_tokens(state)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 16
    d.ellipse([pad, pad, size - pad, size - pad], fill=tokens["field"], outline=tokens["dot"], width=max(2, size // 16))
    inner = size // 4
    d.ellipse([inner, inner, size - inner, size - inner], fill=tokens["dot"])
    return img
