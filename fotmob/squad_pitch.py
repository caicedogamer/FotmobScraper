"""
squad_pitch.py
--------------
Renders a user's squad sheet as a formation image.

Reuses pitch drawing primitives from fotmob.pitch.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from fotmob.pitch import (
    _draw_pitch,
    _font,
    _px,
    _bright,
    BG_DARK,
    TEXT_W,
    TEXT_DIM,
    GOLD,
)

# ── Rarity colours ─────────────────────────────────────────────────────────────

RARITY_BORDER: dict[str, tuple[int, int, int]] = {
    "common":    (149, 165, 166),
    "uncommon":  (46,  204, 113),
    "rare":      (52,  152, 219),
    "elite":     (155,  89, 182),
    "legendary": (241, 196,  15),
    "mythic":    (255,  77, 210),
}

EMPTY_FILL   = (38,  50,  68)
EMPTY_BORDER = (65,  85, 108)
HEADER_BG    = (12,  20,  38)
ACCENT       = GOLD


def _card_fill(rating) -> tuple[int, int, int]:
    """Map an integer card rating to a fill colour for the circle."""
    try:
        r = int(rating)
        if r >= 93: return (170,  20, 200)
        if r >= 88: return (200, 160,   0)
        if r >= 83: return (120,  60, 170)
        if r >= 75: return (40,  120, 200)
        if r >= 65: return (30,  160,  80)
        return (80,  90, 105)
    except (TypeError, ValueError):
        return (80,  90, 105)


def _short_name(name: str, max_len: int = 12) -> str:
    if len(name) <= max_len:
        return name
    parts = name.split()
    if len(parts) >= 2:
        abbr = f"{parts[0][0]}. {parts[-1]}"
        if len(abbr) <= max_len:
            return abbr
    return name[:max_len - 1] + "."


# ── Circle drawing ─────────────────────────────────────────────────────────────

def _draw_slot(
    draw:       ImageDraw.ImageDraw,
    cx:         int,
    cy:         int,
    card:       dict | None,
    slot_label: str,
    r:          int = 28,
) -> None:
    if card is None:
        _draw_empty_slot(draw, cx, cy, slot_label, r)
    else:
        _draw_card_slot(draw, cx, cy, card, r)


def _draw_empty_slot(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, label: str, r: int
) -> None:
    draw.ellipse([cx-r+3, cy-r+3, cx+r+3, cy+r+3], fill=(0, 0, 0))
    draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                 fill=EMPTY_FILL, outline=EMPTY_BORDER, width=2)

    f_pos = _font(13, bold=True)
    pb = draw.textbbox((0, 0), label, font=f_pos)
    pw, ph = pb[2] - pb[0], pb[3] - pb[1]
    draw.text((cx - pw // 2, cy - ph // 2 - 1), label,
              font=f_pos, fill=(100, 120, 145))

    f_empty = _font(10)
    eb = draw.textbbox((0, 0), "Empty", font=f_empty)
    ew = eb[2] - eb[0]
    draw.text((cx - ew // 2, cy + r + 4), "Empty",
              font=f_empty, fill=(65, 80, 100))


def _draw_card_slot(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, card: dict, r: int
) -> None:
    fill   = _card_fill(card.get("rating"))
    rarity = card.get("rarity", "common")
    border = RARITY_BORDER.get(rarity, RARITY_BORDER["common"])

    # Drop shadow
    draw.ellipse([cx-r+4, cy-r+4, cx+r+4, cy+r+4], fill=(0, 0, 0))

    # Glow ring for high-rarity cards
    if rarity in ("legendary", "mythic"):
        for gw in (8, 5):
            alpha = 90 if gw == 8 else 200
            r_border_rgba = (*border, alpha)
            draw.ellipse([cx-r-gw, cy-r-gw, cx+r+gw, cy+r+gw],
                         outline=r_border_rgba, width=2)

    # Main circle
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=fill, outline=border, width=3)

    # Rating label
    f_centre = _font(17, bold=True)
    rating_str = str(card.get("rating", "?"))
    txt_col = (18, 18, 18) if _bright(fill) else TEXT_W
    rb = draw.textbbox((0, 0), rating_str, font=f_centre)
    rw, rh = rb[2] - rb[0], rb[3] - rb[1]
    draw.text((cx - rw // 2, cy - rh // 2 - 1), rating_str,
              font=f_centre, fill=txt_col)

    # Player name below circle (shadow + white)
    f_name = _font(12)
    name_str = _short_name(card.get("name", "?"))
    nb = draw.textbbox((0, 0), name_str, font=f_name)
    nw = nb[2] - nb[0]
    ny = cy + r + 4
    draw.text((cx - nw // 2 + 1, ny + 1), name_str, font=f_name, fill=(0, 0, 0))
    draw.text((cx - nw // 2,     ny),     name_str, font=f_name, fill=TEXT_W)

    # Small rarity tag — coloured dot top-right
    dot_cx = cx + r - 3
    dot_cy = cy - r + 3
    dot_r  = 6
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=border, outline=(0, 0, 0), width=1,
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def draw_squad_image(
    formation:  str,
    slots:      dict[str, dict | None],
    slot_defs:  list[dict],
    user_name:  str = "Your Club",
) -> bytes:
    """
    Render a squad formation as a PNG image.

    Args:
        formation: Formation string, e.g. ``"4-3-3"``.
        slots:     Mapping of slot_key → card dict (or None for empty).
        slot_defs: List of slot definition dicts from ``FORMATIONS[formation]``.
        user_name: Display name shown in the header.

    Returns:
        PNG bytes suitable for ``discord.File``.
    """
    W,  H   = 640, 870
    PX1, PX2 = 30,  610
    PY1, PY2 = 88,  838

    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # ── Header ──────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 82], fill=HEADER_BG)
    draw.line([0, 82, W, 82], fill=(35, 55, 90), width=2)
    draw.rectangle([0,   0,  5, 82], fill=ACCENT)
    draw.rectangle([W-5, 0,  W, 82], fill=ACCENT)

    f_title = _font(20, bold=True)
    f_sub   = _font(13)

    filled  = sum(1 for v in slots.values() if v is not None)
    title   = user_name
    subtitle = f"{formation}  ·  {filled}/11 positions filled"

    tb = draw.textbbox((0, 0), title, font=f_title)
    draw.text((W // 2 - (tb[2]-tb[0]) // 2, 10), title,
              font=f_title, fill=TEXT_W)
    sb = draw.textbbox((0, 0), subtitle, font=f_sub)
    draw.text((W // 2 - (sb[2]-sb[0]) // 2, 46), subtitle,
              font=f_sub, fill=TEXT_DIM)

    # ── Pitch ────────────────────────────────────────────────────────────────────
    _draw_pitch(draw, PX1, PY1, PX2, PY2)
    draw = ImageDraw.Draw(img)   # re-acquire after any pitch compositing

    # ── Player slots ─────────────────────────────────────────────────────────────
    # y_norm=0 (GK) maps to bottom of pitch; y_norm=1 (ST) maps to top.
    usable_h = PY2 - PY1 - 45   # leave padding at both ends for labels

    for slot_def in slot_defs:
        key    = slot_def["key"]
        x_norm = slot_def["x"]
        y_norm = slot_def["y"]

        cx = _px(x_norm, PX1, PX2)
        cy = int(PY2 - 22 - y_norm * usable_h)

        _draw_slot(draw, cx, cy, slots.get(key), slot_def["label"])

    # ── Legend ────────────────────────────────────────────────────────────────────
    f_leg = _font(11)
    legend_items = [
        (RARITY_BORDER["mythic"],    "Mythic"),
        (RARITY_BORDER["legendary"], "Legendary"),
        (RARITY_BORDER["elite"],     "Elite"),
        (RARITY_BORDER["rare"],      "Rare"),
        (RARITY_BORDER["uncommon"],  "Uncommon"),
        (RARITY_BORDER["common"],    "Common"),
    ]
    lx = PX1
    ly = PY2 + 6
    for col, lbl in legend_items:
        draw.ellipse([lx, ly + 3, lx + 10, ly + 13], fill=col)
        lb = draw.textbbox((0, 0), lbl, font=f_leg)
        draw.text((lx + 14, ly), lbl, font=f_leg, fill=TEXT_DIM)
        lx += 14 + (lb[2] - lb[0]) + 8

    # ── Footer hint ───────────────────────────────────────────────────────────────
    hint = "/squad_place position:<slot> inventory_id:<id>  ·  /inventory to see your cards"
    f_hint = _font(10)
    hb = draw.textbbox((0, 0), hint, font=f_hint)
    draw.text((W // 2 - (hb[2]-hb[0]) // 2, H - 16), hint,
              font=f_hint, fill=(48, 62, 82))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()
