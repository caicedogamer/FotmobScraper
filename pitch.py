"""
pitch.py
--------
Generates a football pitch image showing both team lineups,
with players coloured by FotMob rating.

Requires: pillow  (pip install pillow)
"""

from __future__ import annotations
import io
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Palette ───────────────────────────────────────────────────────────────────

BG_DARK     = (10,  14,  20)
BG_HEADER   = (16,  22,  34)
PITCH_A     = (48, 118,  44)
PITCH_B     = (56, 136,  50)
LINE_W      = (255, 255, 255)
LINE_DIM    = (200, 200, 200, 80)    # semi-transparent for formation lines
TEXT_W      = (255, 255, 255)
TEXT_DIM    = (140, 140, 155)
HOME_BORDER = (100, 180, 255)        # blue border for home players
AWAY_BORDER = (255, 100, 100)        # red border for away players
GOLD        = (255, 215,   0)


def _rating_colour(rating) -> tuple[int, int, int]:
    if rating is None:
        return (80, 100, 115)
    try:
        r = float(str(rating).replace(",", "."))
        if r >= 8.5: return (0,   210,  90)
        if r >= 7.5: return (90,  220,  20)
        if r >= 7.0: return (190, 250,   0)
        if r >= 6.5: return (255, 210,   0)
        if r >= 6.0: return (255, 130,   0)
        if r >= 5.0: return (255,  80,  30)
        return (230, 50, 50)
    except (ValueError, TypeError):
        return (80, 100, 115)


def _bright(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) > 145


# ── Font loading ──────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates += [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/ArialBd.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    candidates += [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


# ── Coordinate helper ─────────────────────────────────────────────────────────

def _px(n: float, lo: int, hi: int) -> int:
    return int(lo + n * (hi - lo))


# ── Pitch drawing ─────────────────────────────────────────────────────────────

def _draw_pitch(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int):
    pw = x2 - x1
    ph = y2 - y1
    lw = 2

    # Alternating vertical stripes (running goal-to-goal, like real grass)
    n = 14
    for i in range(n):
        sx = x1 + int(i * pw / n)
        ex = x1 + int((i + 1) * pw / n)
        draw.rectangle([sx, y1, ex, y2], fill=PITCH_A if i % 2 == 0 else PITCH_B)

    # Outer border
    draw.rectangle([x1, y1, x2, y2], outline=LINE_W, width=lw)

    # Halfway line
    mid_y = _px(0.5, y1, y2)
    draw.line([x1, mid_y, x2, mid_y], fill=LINE_W, width=lw)

    # Centre D-arcs — semicircle into each half (halfway line remains visible between them)
    ccx = _px(0.5, x1, x2)
    cr  = int(pw * 0.082)
    draw.arc([ccx - cr, mid_y - cr, ccx + cr, mid_y + cr],
             start=180, end=360, fill=LINE_W, width=lw)   # D into home half (above mid)
    draw.arc([ccx - cr, mid_y - cr, ccx + cr, mid_y + cr],
             start=0,   end=180, fill=LINE_W, width=lw)   # D into away half (below mid)
    draw.ellipse([ccx - 4, mid_y - 4, ccx + 4, mid_y + 4], fill=LINE_W)

    # Corner arcs
    arc_r = int(pw * 0.025)
    corners = [
        (x1, y1,  0,  90),
        (x2, y1, 90, 180),
        (x1, y2, 270, 360),
        (x2, y2, 180, 270),
    ]
    for cx, cy, sa, ea in corners:
        draw.arc([cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r],
                 start=sa, end=ea, fill=LINE_W, width=lw)

    for top in (True, False):
        # Penalty area
        bx1 = _px(0.18, x1, x2)
        bx2 = _px(0.82, x1, x2)
        by1, by2 = (y1, _px(0.165, y1, y2)) if top else (_px(0.835, y1, y2), y2)
        draw.rectangle([bx1, by1, bx2, by2], outline=LINE_W, width=lw)
        # Goal box
        gx1 = _px(0.37, x1, x2)
        gx2 = _px(0.63, x1, x2)
        gy1, gy2 = (y1, _px(0.065, y1, y2)) if top else (_px(0.935, y1, y2), y2)
        draw.rectangle([gx1, gy1, gx2, gy2], outline=LINE_W, width=lw)
        # Goal net area (filled darker)
        net_h = int(ph * 0.018)
        if top:
            draw.rectangle([gx1 + lw, y1 + lw, gx2 - lw, y1 + net_h], fill=(20, 20, 20))
        else:
            draw.rectangle([gx1 + lw, y2 - net_h, gx2 - lw, y2 - lw], fill=(20, 20, 20))
        # Penalty spot
        sx  = ccx
        spy = _px(0.115 if top else 0.885, y1, y2)
        draw.ellipse([sx - 4, spy - 4, sx + 4, spy + 4], fill=LINE_W)
        # Penalty arc — shifted toward midfield so the D is clearly outside the box
        ar = int(pw * 0.05)
        arc_offset = int(ph * 0.05)
        spy_arc = (spy + arc_offset) if top else (spy - arc_offset)
        sa_arc = 0 if top else 180
        ea_arc = 180 if top else 360
        draw.arc([sx - ar, spy_arc - ar, sx + ar, spy_arc + ar],
                 start=sa_arc, end=ea_arc, fill=LINE_W, width=lw)


# ── Formation connection lines ────────────────────────────────────────────────

def _assign_cy(lineup: list[dict], flip: bool = False):
    """
    Normalise each player's y_norm to a 0-1 t value within their team's range.
    flip=True reverses direction so GK (low y_norm) ends up at t=1 (bottom of their half).
    Stores result in player["_cy_t"].
    """
    starters = [p for p in lineup if p.get("starter")]
    if not starters:
        for p in lineup:
            p["_cy_t"] = 0.5
        return
    ys = [p.get("y_norm", 0.5) for p in starters]
    y_min, y_max = min(ys), max(ys)
    span = max(y_max - y_min, 0.01)
    for p in lineup:
        t = (p.get("y_norm", 0.5) - y_min) / span
        p["_cy_t"] = (1.0 - t) if flip else t


def _draw_formation_lines(
    img:    Image.Image,
    lineup: list[dict],
    PX1: int, PX2: int, y_lo: int, y_hi: int,
    colour: tuple,
):
    """Draw subtle lines connecting players in the same formation row."""
    starters = [p for p in lineup if p.get("starter")]
    groups: dict[str, list] = defaultdict(list)
    for p in starters:
        key = f"{p.get('_cy_t', 0.5):.2f}"
        groups[key].append(p)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for players in groups.values():
        if len(players) < 2:
            continue
        ordered = sorted(players, key=lambda p: p.get("x_norm", 0.5))
        for i in range(len(ordered) - 1):
            p1, p2 = ordered[i], ordered[i + 1]
            x1c = _px(p1["x_norm"], PX1, PX2)
            y1c = int(y_lo + p1["_cy_t"] * (y_hi - y_lo))
            x2c = _px(p2["x_norm"], PX1, PX2)
            y2c = int(y_lo + p2["_cy_t"] * (y_hi - y_lo))
            d.line([x1c, y1c, x2c, y2c], fill=(*colour, 90), width=2)

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


# ── Name helper ───────────────────────────────────────────────────────────────

def _short_name(name: str, max_len: int = 12) -> str:
    if len(name) <= max_len:
        return name
    parts = name.split()
    if len(parts) >= 2:
        abbr = f"{parts[0][0]}. {parts[-1]}"
        if len(abbr) <= max_len:
            return abbr
    return name[:max_len - 1] + "."


# ── Player circle ─────────────────────────────────────────────────────────────

def _draw_player(
    draw:        ImageDraw.ImageDraw,
    cx:          int,
    cy:          int,
    player:      dict,
    team_border: tuple,
    highlighted: bool = False,
    r:           int  = 30,
):
    rating  = player.get("rating")
    fill    = _rating_colour(rating)
    border  = GOLD if highlighted else team_border
    bw      = 4 if highlighted else 2

    # Drop shadow (offset ellipse in dark colour)
    so = 4
    draw.ellipse([cx - r + so, cy - r + so, cx + r + so, cy + r + so],
                 fill=(0, 0, 0))

    # MOTM outer glow ring
    if player.get("motm"):
        for gw in (8, 5):
            draw.ellipse([cx - r - gw, cy - r - gw, cx + r + gw, cy + r + gw],
                         outline=(*GOLD, 120 if gw == 8 else 220), width=2)

    # Main circle fill + border
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 fill=fill, outline=border, width=bw)

    # Rating / shirt number in centre
    f_centre = _font(16, bold=True)
    label    = str(rating) if rating is not None else str(player.get("shirt") or "?")
    txt_col  = (15, 15, 15) if _bright(fill) else (255, 255, 255)
    bb = draw.textbbox((0, 0), label, font=f_centre)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((cx - tw // 2, cy - th // 2 - 1), label, font=f_centre, fill=txt_col)

    # Player name below — shadow then white
    f_name   = _font(13)
    name_str = _short_name(player.get("name") or "?")
    nb = draw.textbbox((0, 0), name_str, font=f_name)
    nw = nb[2] - nb[0]
    ny = cy + r + 5
    draw.text((cx - nw // 2 + 1, ny + 1), name_str, font=f_name, fill=(0, 0, 0))
    draw.text((cx - nw // 2,     ny),     name_str, font=f_name, fill=TEXT_W)

    # Shirt number — small, top-left of circle
    shirt = player.get("shirt")
    if shirt is not None and rating is not None:
        f_shirt = _font(10)
        draw.text((cx - r + 3, cy - r + 2), str(shirt), font=f_shirt,
                  fill=(230, 230, 230) if _bright(fill) else (200, 200, 200))

    # ── Event badges (top-right of circle) ───────────────────────────────────
    bx = cx + r - 2
    by = cy - r - 16

    # Goals: small white circles with "G"
    goals = player.get("goals") or 0
    f_badge = _font(9, bold=True)
    for i in range(min(goals, 3)):
        gx = bx - i * 13
        draw.ellipse([gx - 8, by - 1, gx + 5, by + 12],
                     fill=(255, 255, 255), outline=(160, 160, 160), width=1)
        draw.text((gx - 5, by + 1), "G", font=f_badge, fill=(0, 0, 0))

    # Cards
    if player.get("yellow"):
        draw.rectangle([bx - 10, by, bx,     by + 13], fill=(255, 210, 0))
    if player.get("red"):
        draw.rectangle([bx + 2,  by, bx + 12, by + 13], fill=(205, 30, 30))


# ── Header drawing ────────────────────────────────────────────────────────────

def _draw_header(
    draw:   ImageDraw.ImageDraw,
    W:      int,
    PX1:    int,
    PX2:    int,
    home:   str,
    away:   str,
    score:  str,
    h_form: str,
    a_form: str,
    league: str,
    date:   str,
):
    # Divider line under header
    draw.rectangle([0, 0, W, 88], fill=BG_HEADER)
    draw.line([0, 88, W, 88], fill=(40, 55, 80), width=2)

    # Coloured accent bars on left (home) and right (away) sides
    draw.rectangle([0, 0, 5, 88], fill=HOME_BORDER)
    draw.rectangle([W - 5, 0, W, 88], fill=AWAY_BORDER)

    f_score = _font(36, bold=True)
    f_team  = _font(17, bold=True)
    f_small = _font(13)

    # Score centred
    sb = draw.textbbox((0, 0), score, font=f_score)
    draw.text((W // 2 - (sb[2] - sb[0]) // 2, 14), score, font=f_score, fill=TEXT_W)

    # Team names
    draw.text((PX1, 6),  home, font=f_team, fill=HOME_BORDER)
    ab = draw.textbbox((0, 0), away, font=f_team)
    draw.text((PX2 - (ab[2] - ab[0]), 6), away, font=f_team, fill=AWAY_BORDER)

    # Formations
    if h_form:
        draw.text((PX1, 32), h_form, font=f_small, fill=(150, 210, 150))
    if a_form:
        afb = draw.textbbox((0, 0), a_form, font=f_small)
        draw.text((PX2 - (afb[2] - afb[0]), 32), a_form, font=f_small, fill=(210, 150, 150))

    # League · date
    info = "  ·  ".join(filter(None, [league, date]))
    if info:
        ib = draw.textbbox((0, 0), info, font=f_small)
        draw.text((W // 2 - (ib[2] - ib[0]) // 2, 58), info, font=f_small, fill=TEXT_DIM)


# ── Legend ────────────────────────────────────────────────────────────────────

def _draw_legend(draw: ImageDraw.ImageDraw, y: int, PX1: int, W: int):
    f = _font(12)
    items = [
        ((0,   210,  90),  "8.5+"),
        ((90,  220,  20),  "7.5+"),
        ((190, 250,   0),  "7.0+"),
        ((255, 210,   0),  "6.5+"),
        ((255, 130,   0),  "6.0+"),
        ((230,  50,  50),  "<6.0"),
        ((80,  100, 115),  "N/A"),
    ]
    lx = PX1
    for colour, label in items:
        draw.ellipse([lx, y + 4, lx + 14, y + 18], fill=colour)
        draw.text((lx + 18, y + 1), label, font=f, fill=TEXT_DIM)
        lx += 66

    # Home/away border key
    kx = W - 230
    draw.ellipse([kx, y + 4, kx + 14, y + 18], fill=(60, 60, 60),
                 outline=HOME_BORDER, width=2)
    draw.text((kx + 18, y + 1), "Home", font=f, fill=HOME_BORDER)
    kx += 72
    draw.ellipse([kx, y + 4, kx + 14, y + 18], fill=(60, 60, 60),
                 outline=AWAY_BORDER, width=2)
    draw.text((kx + 18, y + 1), "Away", font=f, fill=AWAY_BORDER)
    kx += 68
    draw.ellipse([kx, y + 4, kx + 14, y + 18], fill=(60, 60, 60),
                 outline=GOLD, width=2)
    draw.text((kx + 18, y + 1), "You", font=f, fill=GOLD)


# ── Main entry point ──────────────────────────────────────────────────────────

def draw_lineup_image(match_data: dict, highlight_id=None) -> bytes:
    """
    Render both team lineups on a pitch image.
    highlight_id — player ID to highlight with a gold ring.
    Returns PNG bytes suitable for discord.File.
    """
    W, H = 960, 1180
    PX1, PY1 = 40, 96
    PX2, PY2 = 920, 1090

    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Header
    home   = match_data.get("home_team", "Home")
    away   = match_data.get("away_team", "Away")
    score  = match_data.get("score",     "?–?")
    h_form = match_data.get("home_formation") or ""
    a_form = match_data.get("away_formation") or ""
    league = match_data.get("league") or ""
    date   = match_data.get("date")   or ""

    _draw_header(draw, W, PX1, PX2, home, away, score, h_form, a_form, league, date)

    # Pitch
    _draw_pitch(draw, PX1, PY1, PX2, PY2)

    # Split pitch into two halves — home at top, away at bottom
    mid_y   = (PY1 + PY2) // 2
    H_LO    = PY1 + 68       # home: GK inside top penalty area (clear of pitch border)
    H_HI    = mid_y - 20     # home: forwards near halfway line
    A_LO    = mid_y + 20     # away: forwards near halfway line
    A_HI    = PY2 - 78       # away: GK inside bottom penalty area (name fits above legend)

    # Side labels
    f_side = _font(13)
    draw.text((PX1 + 8, PY1 + 8),  f"↓  {home}", font=f_side, fill=(*HOME_BORDER, 200))
    draw.text((PX1 + 8, PY2 - 24), f"↑  {away}", font=f_side, fill=(*AWAY_BORDER, 200))

    home_lineup = match_data.get("home_lineup", [])
    away_lineup = match_data.get("away_lineup", [])

    # FotMob coords are team-relative: y≈0 = own goal, y≈0.5 = midfield
    # Home: GK(t=0)→H_LO(top),  forwards(t=1)→H_HI  — flip=False
    # Away: GK(t=0)→A_HI(bottom), forwards(t=1)→A_LO — flip=True
    _assign_cy(home_lineup, flip=False)
    _assign_cy(away_lineup, flip=True)

    # Formation lines (drawn before players so circles sit on top)
    _draw_formation_lines(img, home_lineup, PX1, PX2, H_LO, H_HI, HOME_BORDER)
    _draw_formation_lines(img, away_lineup, PX1, PX2, A_LO, A_HI, AWAY_BORDER)
    draw = ImageDraw.Draw(img)  # re-acquire draw handle after paste

    # Players
    def _draw_players(lineup: list[dict], team_border: tuple, y_lo: int, y_hi: int):
        for player in lineup:
            if not player.get("starter"):
                continue
            cx = _px(player.get("x_norm", 0.5), PX1, PX2)
            cy = int(y_lo + player.get("_cy_t", 0.5) * (y_hi - y_lo))
            hl = (str(player.get("id")) == str(highlight_id)) if highlight_id else False
            _draw_player(draw, cx, cy, player, team_border=team_border, highlighted=hl, r=30)

    _draw_players(home_lineup, HOME_BORDER, H_LO, H_HI)
    _draw_players(away_lineup, AWAY_BORDER, A_LO, A_HI)

    # Legend
    _draw_legend(draw, PY2 + 14, PX1, W)

    # Export
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()
