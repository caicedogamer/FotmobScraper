"""
bot.py
------
Discord bot for FotMob player stats.

Commands:
    /player <name>               — full profile with form strip
    /stats  <name>               — season stats with key metrics
    /matches <name> [count]      — recent match log
    /career <name>               — career history
    /compare <player1> <player2> — side-by-side goal contributions

Setup:
    pip install discord.py
    Set DISCORD_TOKEN in .env
    python bot.py
"""

import asyncio
import io
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import list_players, load_player
from scraper import (
    search_players, make_session,
    fetch_player_json, parse_player,
    fetch_match_json, parse_match,
)
from pitch import draw_lineup_image
from predictor import get_predictions, LEAGUES

load_dotenv(Path(__file__).parent / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FOTMOB_BASE = "https://www.fotmob.com"

# ── Colours ───────────────────────────────────────────────────────────────────
C_FW      = 0xe74c3c   # red    — forwards
C_MF      = 0x3498db   # blue   — midfielders
C_DF      = 0x2ecc71   # green  — defenders
C_GK      = 0xe67e22   # orange — goalkeepers
C_DEFAULT = 0x9b59b6   # purple — unknown
C_GOLD    = 0xf1c40f
C_WIN     = 0x27ae60
C_DRAW    = 0x95a5a6
C_LOSS    = 0xe74c3c

POSITION_COLOUR = {
    "forward":    C_FW,
    "striker":    C_FW,
    "winger":     C_FW,
    "midfielder": C_MF,
    "defender":   C_DF,
    "back":       C_DF,
    "goalkeeper": C_GK,
    "keeper":     C_GK,
}

RESULT_EMOJI  = {"W": "🟢", "D": "🟡", "L": "🔴"}
RESULT_COLOUR = {"W": C_WIN, "D": C_DRAW, "L": C_LOSS}

FLAG_MAP = {
    "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Spain": "🇪🇸", "Germany": "🇩🇪", "France": "🇫🇷",
    "Brazil": "🇧🇷", "Argentina": "🇦🇷", "Portugal": "🇵🇹", "Netherlands": "🇳🇱",
    "Belgium": "🇧🇪", "Italy": "🇮🇹", "Norway": "🇳🇴", "Denmark": "🇩🇰",
    "Sweden": "🇸🇪", "Poland": "🇵🇱", "Croatia": "🇭🇷", "Serbia": "🇷🇸",
    "Uruguay": "🇺🇾", "Colombia": "🇨🇴", "Morocco": "🇲🇦", "Senegal": "🇸🇳",
    "Nigeria": "🇳🇬", "Egypt": "🇪🇬", "Japan": "🇯🇵", "South Korea": "🇰🇷",
    "United States": "🇺🇸", "Mexico": "🇲🇽", "Australia": "🇦🇺",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "Ireland": "🇮🇪",
    "Switzerland": "🇨🇭", "Austria": "🇦🇹", "Czech Republic": "🇨🇿",
    "Slovakia": "🇸🇰", "Hungary": "🇭🇺", "Ukraine": "🇺🇦", "Russia": "🇷🇺",
    "Turkey": "🇹🇷", "Greece": "🇬🇷", "Romania": "🇷🇴", "Bulgaria": "🇧🇬",
    "Ivory Coast": "🇨🇮", "Ghana": "🇬🇭", "Cameroon": "🇨🇲",
    "Ecuador": "🇪🇨", "Chile": "🇨🇱", "Peru": "🇵🇪", "Paraguay": "🇵🇾",
    "Venezuela": "🇻🇪", "Bolivia": "🇧🇴", "Jamaica": "🇯🇲",
    "China": "🇨🇳", "Saudi Arabia": "🇸🇦", "Iran": "🇮🇷",
    "Algeria": "🇩🇿", "Tunisia": "🇹🇳", "Mali": "🇲🇱", "Guinea": "🇬🇳",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _or(val, fallback="N/A"):
    if val is None or str(val).strip() == "":
        return fallback
    return val


def _flag(nationality: str) -> str:
    return FLAG_MAP.get(nationality, "")


def _position_colour(position: str) -> int:
    if not position:
        return C_DEFAULT
    pl = position.lower()
    for key, colour in POSITION_COLOUR.items():
        if key in pl:
            return colour
    return C_DEFAULT


def _form_strip(matches: list, n: int = 5) -> str:
    """e.g. 🟢🟢🔴🟡🟢"""
    return "".join(RESULT_EMOJI.get(m.get("result"), "⚪") for m in matches[:n]) or "—"


def _dominant_result_colour(matches: list, n: int = 5) -> int:
    recent = matches[:n]
    w = sum(1 for m in recent if m.get("result") == "W")
    l = sum(1 for m in recent if m.get("result") == "L")
    if w >= 3: return C_WIN
    if l >= 3: return C_LOSS
    return C_DRAW


def _goal_contributions(stats: dict) -> tuple[int, int, int]:
    goals, assists = 0, 0
    for k, v in stats.items():
        kl = k.lower()
        try:
            if kl in ("goals", "goal"):
                goals = int(float(v))
            elif kl in ("assists", "assist"):
                assists = int(float(v))
        except (TypeError, ValueError):
            pass
    return goals, assists, goals + assists


def _pick_stats(stats: dict) -> dict:
    """Pull out the most interesting attacking/performance stats."""
    priority = [
        "Goals", "Assists", "Goal contributions", "Expected goals (xG)",
        "Expected assists (xA)", "Shots", "Shots on target", "Key passes",
        "Successful dribbles", "Accurate passes", "Tackles won",
        "Interceptions", "Clearances", "Saves", "Clean sheets",
        "Minutes played", "Matches played", "Rating",
    ]
    ordered = {}
    for p in priority:
        for k, v in stats.items():
            if k.lower() == p.lower() and k not in ordered:
                ordered[k] = v
    for k, v in stats.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def _stat_lines(stats: dict, limit: int = 16) -> str:
    if not stats:
        return "*No stats available*"
    ordered = _pick_stats(stats)
    lines = []
    for k, v in list(ordered.items())[:limit]:
        bar = ""
        # add a small visual bar for percentage-like values
        try:
            fv = float(v)
            if 0 < fv <= 100 and ("%" in k or "accuracy" in k.lower() or "success" in k.lower()):
                filled = round(fv / 10)
                bar = f"  {'█' * filled}{'░' * (10 - filled)}"
        except (TypeError, ValueError):
            pass
        lines.append(f"`{_or(v):>7}`  {k}{bar}")
    return "\n".join(lines)


def _match_lines(matches: list, limit: int = 8) -> str:
    if not matches:
        return "*No recent matches*"
    lines = []
    for m in matches[:limit]:
        r      = m.get("result", "?")
        emoji  = RESULT_EMOJI.get(r, "⚪")
        motm   = " ⭐" if m.get("motm") else ""
        g      = m.get("goals") or 0
        a      = m.get("assists") or 0
        rating = m.get("rating")
        score  = _or(m.get("score"), "?–?")
        fix    = _or(m.get("fixture"), "Unknown fixture")
        # trim long fixture names
        if len(fix) > 30:
            parts = fix.split(" vs ")
            if len(parts) == 2:
                fix = f"{parts[0][:13].strip()} vs {parts[1][:13].strip()}"
        rating_str = f"  ⭐`{rating}`" if rating else ""
        ga_str = f"  ⚽{g} 🎯{a}" if (g or a) else ""
        lines.append(f"{emoji} **{score}** {fix}{ga_str}{rating_str}{motm}")
    return "\n".join(lines)


def _career_lines(career: list) -> str:
    if not career:
        return "*No career data*"
    lines = []
    for c in career:
        team   = _or(c.get("team"), "Unknown")
        start  = (_or(c.get("start"), ""))[:4]
        end    = (_or(c.get("end"), ""))[:4] or "now"
        apps   = c.get("appearances") or "–"
        goals  = c.get("goals")       or "–"
        assts  = c.get("assists")     or "–"
        period = f"{start}–{end}" if start else end
        lines.append(f"**{team}** `{period}`  {apps} apps  ⚽{goals}  🎯{assts}")
    return "\n".join(lines)


async def _resolve_player(name: str) -> dict | None:
    loop = asyncio.get_event_loop()

    stored = await loop.run_in_executor(None, list_players)
    name_lower = name.lower()
    for p in stored:
        if name_lower in (p.get("name") or "").lower():
            return await loop.run_in_executor(None, load_player, p["id"])

    results = await loop.run_in_executor(None, search_players, name)
    if not results:
        return None

    top = results[0]

    def _scrape():
        session = make_session()
        raw = fetch_player_json(session, top["id"], top["slug"])
        return parse_player(raw)

    return await loop.run_in_executor(None, _scrape)


def _not_found_embed(name: str) -> discord.Embed:
    return discord.Embed(
        title="Player not found",
        description=f"No results for **{name}**.\nCheck the spelling or try a different name.",
        colour=C_LOSS,
    )


# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user} ({bot.user.id})")


# ── /player ───────────────────────────────────────────────────────────────────

@tree.command(name="player", description="Full player profile — bio, form, goal contributions")
@app_commands.describe(name="Player name (e.g. Erling Haaland)")
async def cmd_player(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    player = await _resolve_player(name)
    if not player:
        await interaction.followup.send(embed=_not_found_embed(name), ephemeral=True)
        return

    stats    = player.get("season_stats", {})
    matches  = player.get("matches", [])
    goals, assists, gc = _goal_contributions(stats)
    nat      = _or(player.get("nationality"), "")
    flag     = _flag(nat)
    pos      = _or(player.get("position"), "")
    colour   = _position_colour(pos)
    form     = _form_strip(matches)
    jersey   = player.get("jersey_number")
    jersey_str = f"  ·  #{jersey}" if jersey else ""

    embed = discord.Embed(
        title=f"{flag}  {_or(player.get('name'))}{jersey_str}",
        url=f"{FOTMOB_BASE}/en/players/{player.get('id')}/{player.get('slug')}",
        colour=colour,
        description=(
            f"**{_or(player.get('club'))}**  ·  {pos}\n"
            f"{nat}  ·  Age {_or(player.get('age'))}"
        ),
    )
    embed.set_thumbnail(url=player.get("image_url"))

    # Goal contributions row
    embed.add_field(name="⚽  Goals",             value=f"**{goals}**",   inline=True)
    embed.add_field(name="🎯  Assists",            value=f"**{assists}**", inline=True)
    embed.add_field(name="🔥  Goal Contributions", value=f"**{gc}**",      inline=True)

    # Recent form
    if matches:
        motm_count = sum(1 for m in matches if m.get("motm"))
        embed.add_field(
            name="📅  Recent Form",
            value=f"{form}\n{'⭐ ' + str(motm_count) + ' MOTM' if motm_count else ''}",
            inline=False,
        )
        # Last 5 match lines
        lines = _match_lines(matches, 5)
        embed.add_field(name="🗓️  Last 5 Matches", value=lines, inline=False)

    embed.set_footer(text="FotMob  ·  Use /stats for full stats  ·  /matches for full log")
    await interaction.followup.send(embed=embed)


# ── /stats ────────────────────────────────────────────────────────────────────

@tree.command(name="stats", description="Season stats with key metrics highlighted")
@app_commands.describe(name="Player name")
async def cmd_stats(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    player = await _resolve_player(name)
    if not player:
        await interaction.followup.send(embed=_not_found_embed(name), ephemeral=True)
        return

    stats  = player.get("season_stats", {})
    goals, assists, gc = _goal_contributions(stats)
    colour = _position_colour(_or(player.get("position"), ""))
    nat    = _or(player.get("nationality"), "")
    flag   = _flag(nat)

    # pull a few extra stats for the headline row
    def _s(key):
        for k, v in stats.items():
            if k.lower() == key.lower():
                return _or(v, "–")
        return "–"

    embed = discord.Embed(
        title=f"📊  {flag} {_or(player.get('name'))} — Season Stats",
        url=f"{FOTMOB_BASE}/en/players/{player.get('id')}/{player.get('slug')}",
        colour=colour,
        description=(
            f"**{_or(player.get('club'))}**  ·  {_or(player.get('position'))}"
        ),
    )
    embed.set_thumbnail(url=player.get("image_url"))

    # Headline numbers
    embed.add_field(name="⚽  Goals",             value=f"**{goals}**",   inline=True)
    embed.add_field(name="🎯  Assists",            value=f"**{assists}**", inline=True)
    embed.add_field(name="🔥  G+A",                value=f"**{gc}**",      inline=True)

    xg  = _s("Expected goals (xG)")
    xa  = _s("Expected assists (xA)")
    rat = _s("Rating")
    if any(v != "–" for v in [xg, xa, rat]):
        embed.add_field(name="📈  xG",     value=xg,  inline=True)
        embed.add_field(name="📈  xA",     value=xa,  inline=True)
        embed.add_field(name="⭐  Rating", value=rat, inline=True)

    embed.add_field(
        name="📋  All Stats",
        value=_stat_lines(stats, 18),
        inline=False,
    )
    embed.set_footer(text=f"FotMob  ·  {nat}")
    await interaction.followup.send(embed=embed)


# ── /matches ──────────────────────────────────────────────────────────────────

@tree.command(name="matches", description="Recent match log with ratings and goal contributions")
@app_commands.describe(name="Player name", count="Matches to show (1–15, default 8)")
async def cmd_matches(interaction: discord.Interaction, name: str, count: int = 8):
    await interaction.response.defer()
    count  = max(1, min(count, 15))
    player = await _resolve_player(name)
    if not player:
        await interaction.followup.send(embed=_not_found_embed(name), ephemeral=True)
        return

    matches     = player.get("matches", [])
    shown       = matches[:count]
    goals_sum   = sum(m.get("goals")   or 0 for m in shown)
    assists_sum = sum(m.get("assists") or 0 for m in shown)
    motms       = sum(1 for m in shown if m.get("motm"))
    wins        = sum(1 for m in shown if m.get("result") == "W")
    draws       = sum(1 for m in shown if m.get("result") == "D")
    losses      = sum(1 for m in shown if m.get("result") == "L")
    form        = _form_strip(matches, count)
    colour      = _dominant_result_colour(matches, count)
    nat         = _or(player.get("nationality"), "")
    flag        = _flag(nat)

    embed = discord.Embed(
        title=f"🗓️  {flag} {_or(player.get('name'))} — Last {min(count, len(matches))} Matches",
        url=f"{FOTMOB_BASE}/en/players/{player.get('id')}/{player.get('slug')}",
        colour=colour,
        description=f"**Form:** {form}",
    )
    embed.set_thumbnail(url=player.get("image_url"))

    embed.add_field(name="⚽  Goals",   value=f"**{goals_sum}**",              inline=True)
    embed.add_field(name="🎯  Assists", value=f"**{assists_sum}**",            inline=True)
    embed.add_field(name="🔥  G+A",     value=f"**{goals_sum + assists_sum}**",inline=True)
    embed.add_field(name="🟢  W",       value=str(wins),   inline=True)
    embed.add_field(name="🟡  D",       value=str(draws),  inline=True)
    embed.add_field(name="🔴  L",       value=str(losses), inline=True)
    if motms:
        embed.add_field(name="⭐  MOTM", value=str(motms), inline=True)

    embed.add_field(name="\u200b", value=_match_lines(matches, count), inline=False)
    embed.set_footer(text=f"FotMob  ·  {_or(player.get('club'))}")
    await interaction.followup.send(embed=embed)


# ── /career ───────────────────────────────────────────────────────────────────

@tree.command(name="career", description="Career history club-by-club")
@app_commands.describe(name="Player name")
async def cmd_career(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    player = await _resolve_player(name)
    if not player:
        await interaction.followup.send(embed=_not_found_embed(name), ephemeral=True)
        return

    career      = player.get("career", [])
    def _int(val):
        try:
            return int(str(val).rstrip("*").strip())
        except (TypeError, ValueError):
            return 0

    total_apps  = sum(_int(c.get("appearances")) for c in career if c.get("appearances"))
    total_goals = sum(_int(c.get("goals"))       for c in career if c.get("goals"))
    total_assts = sum(_int(c.get("assists"))     for c in career if c.get("assists"))
    nat         = _or(player.get("nationality"), "")
    flag        = _flag(nat)
    colour      = _position_colour(_or(player.get("position"), ""))

    embed = discord.Embed(
        title=f"🏟️  {flag} {_or(player.get('name'))} — Career",
        url=f"{FOTMOB_BASE}/en/players/{player.get('id')}/{player.get('slug')}",
        colour=colour,
        description=f"**{_or(player.get('club'))}**  ·  {_or(player.get('position'))}",
    )
    embed.set_thumbnail(url=player.get("image_url"))

    embed.add_field(name="🏟️  Clubs",         value=f"**{len(career)}**",              inline=True)
    embed.add_field(name="👟  Career Apps",    value=f"**{total_apps}**",              inline=True)
    embed.add_field(name="\u200b",             value="\u200b",                         inline=True)
    embed.add_field(name="⚽  Career Goals",   value=f"**{total_goals}**",             inline=True)
    embed.add_field(name="🎯  Career Assists", value=f"**{total_assts}**",             inline=True)
    embed.add_field(name="🔥  Career G+A",     value=f"**{total_goals + total_assts}**", inline=True)

    lines = _career_lines(career)
    # Split into chunks if too long (Discord field limit 1024)
    if len(lines) > 1020:
        lines = lines[:1020] + "\n*…trimmed*"
    embed.add_field(name="📋  Club History", value=lines, inline=False)

    embed.set_footer(text="FotMob  ·  Senior career")
    await interaction.followup.send(embed=embed)


# ── /compare ──────────────────────────────────────────────────────────────────

@tree.command(name="compare", description="Side-by-side comparison of two players")
@app_commands.describe(player1="First player name", player2="Second player name")
async def cmd_compare(interaction: discord.Interaction, player1: str, player2: str):
    await interaction.response.defer()

    p1, p2 = await asyncio.gather(_resolve_player(player1), _resolve_player(player2))

    missing = []
    if not p1: missing.append(player1)
    if not p2: missing.append(player2)
    if missing:
        await interaction.followup.send(
            embed=discord.Embed(
                title="Players not found",
                description="\n".join(f"❌ **{m}**" for m in missing),
                colour=C_LOSS,
            ),
            ephemeral=True,
        )
        return

    def _stats(player):
        stats   = player.get("season_stats", {})
        matches = player.get("matches", [])
        g, a, gc = _goal_contributions(stats)
        mg   = sum(m.get("goals")   or 0 for m in matches)
        ma   = sum(m.get("assists") or 0 for m in matches)
        motm = sum(1 for m in matches if m.get("motm"))
        wins = sum(1 for m in matches if m.get("result") == "W")
        return g, a, gc, mg, ma, motm, wins

    g1, a1, gc1, mg1, ma1, motm1, w1 = _stats(p1)
    g2, a2, gc2, mg2, ma2, motm2, w2 = _stats(p2)

    n1   = _or(p1.get("name"))
    n2   = _or(p2.get("name"))
    flag1 = _flag(_or(p1.get("nationality"), ""))
    flag2 = _flag(_or(p2.get("nationality"), ""))

    def _medal(v1, v2, flip=False):
        if v1 == v2:   return "🟰", "🟰"
        winner = (v1 > v2) ^ flip
        return ("🥇", "🥈") if winner else ("🥈", "🥇")

    rows = [
        ("🔥  Goal Contributions", gc1,   gc2,   _medal(gc1,   gc2)),
        ("⚽  Season Goals",        g1,    g2,    _medal(g1,    g2)),
        ("🎯  Season Assists",      a1,    a2,    _medal(a1,    a2)),
        ("📅  Match Goals",         mg1,   mg2,   _medal(mg1,   mg2)),
        ("📅  Match Assists",       ma1,   ma2,   _medal(ma1,   ma2)),
        ("⭐  MOTM Awards",         motm1, motm2, _medal(motm1, motm2)),
        ("🟢  Wins",                w1,    w2,    _medal(w1,    w2)),
    ]

    embed = discord.Embed(
        title=f"⚔️  {flag1} {n1}  vs  {flag2} {n2}",
        colour=C_GOLD,
    )
    embed.set_author(name=n1, icon_url=p1.get("image_url"))
    embed.set_thumbnail(url=p2.get("image_url"))

    # Each row becomes two inline fields with the stat label above
    for label, v1, v2, (m1, m2) in rows:
        embed.add_field(name=label,    value=f"{m1}  **{v1}**", inline=True)
        embed.add_field(name="\u200b", value=f"**{v2}**  {m2}", inline=True)
        embed.add_field(name="\u200b", value="\u200b",           inline=True)  # force new row

    # Club + form comparison
    form1 = _form_strip(p1.get("matches", []))
    form2 = _form_strip(p2.get("matches", []))
    embed.add_field(name=f"📋  {n1}", value=f"{_or(p1.get('club'))}\n{form1}", inline=True)
    embed.add_field(name=f"📋  {n2}", value=f"{_or(p2.get('club'))}\n{form2}", inline=True)

    embed.set_footer(text="🥇 leads  ·  🟰 tied  ·  Data from FotMob")
    await interaction.followup.send(embed=embed)


# ── /match ───────────────────────────────────────────────────────────────────

@tree.command(name="match", description="Show lineup and details for a player's recent match")
@app_commands.describe(
    name="Player name",
    number="Which match to show — 1 = most recent (default), 2 = second most recent, etc.",
)
async def cmd_match(interaction: discord.Interaction, name: str, number: int = 1):
    await interaction.response.defer()
    number = max(1, min(number, 15))

    player = await _resolve_player(name)
    if not player:
        await interaction.followup.send(embed=_not_found_embed(name), ephemeral=True)
        return

    matches = player.get("matches", [])
    if not matches:
        await interaction.followup.send(
            embed=discord.Embed(title="No matches found", colour=C_LOSS,
                                description=f"No recent matches stored for **{_or(player.get('name'))}**."),
            ephemeral=True,
        )
        return

    if number > len(matches):
        await interaction.followup.send(
            embed=discord.Embed(title="Not enough matches", colour=C_LOSS,
                                description=f"Only **{len(matches)}** matches available."),
            ephemeral=True,
        )
        return

    target = matches[number - 1]
    match_url = target.get("url")
    if not match_url:
        await interaction.followup.send(
            embed=discord.Embed(title="No URL", colour=C_LOSS,
                                description="No match page URL stored for that match."),
            ephemeral=True,
        )
        return

    # Scrape match data
    loop = asyncio.get_event_loop()
    try:
        def _scrape():
            session = make_session()
            raw = fetch_match_json(session, match_url)
            return parse_match(raw)
        match_data = await loop.run_in_executor(None, _scrape)
    except Exception as exc:
        await interaction.followup.send(
            embed=discord.Embed(title="Scrape failed", colour=C_LOSS,
                                description=f"```{exc}```"),
            ephemeral=True,
        )
        return

    # ── Build embed ───────────────────────────────────────────────────────────
    result   = target.get("result", "?")
    colour   = RESULT_COLOUR.get(result, C_DRAW)
    r_emoji  = RESULT_EMOJI.get(result, "⚪")
    score    = match_data["score"]
    home     = match_data["home_team"]
    away     = match_data["away_team"]
    league   = _or(match_data.get("league"))
    venue    = _or(match_data.get("venue"), "")
    date     = _or(match_data.get("date"))

    embed = discord.Embed(
        title=f"{r_emoji}  {home} {score} {away}",
        url=f"https://www.fotmob.com{match_url}" if match_url.startswith("/") else match_url,
        colour=colour,
        description=(
            f"📅 {date}  ·  {league}"
            + (f"\n🏟️ {venue}" if venue and venue != "N/A" else "")
        ),
    )
    embed.set_thumbnail(url=player.get("image_url"))

    # Key events (above the image)
    events = match_data.get("events") or []
    if events:
        ev_lines = []
        for ev in events[:12]:
            minute = ev.get("minute")
            etype  = ev.get("type", "")
            pname  = ev.get("player", "")
            detail = ev.get("detail", "")
            icon   = {"Goal": "⚽", "AddedGoal": "⚽", "OwnGoal": "🔴",
                      "Card": "🟨", "SubstitutionIn": "🔄"}.get(etype, "•")
            mins   = f"`{minute}'`" if minute else ""
            ev_lines.append(f"{icon} {mins} {pname}" + (f" *({detail})*" if detail else ""))
        embed.add_field(name="📌  Key Events", value="\n".join(ev_lines), inline=False)

    embed.set_footer(text=f"FotMob  ·  Circle = rating · gold ring = searched player · ⭐ = MOTM")

    # Generate pitch image and attach it to the embed
    pid = player.get("id")
    loop = asyncio.get_event_loop()
    try:
        img_bytes = await loop.run_in_executor(
            None, lambda: draw_lineup_image(match_data, highlight_id=pid)
        )
        file = discord.File(io.BytesIO(img_bytes), filename="lineup.png")
        embed.set_image(url="attachment://lineup.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception:
        # Image generation failed — send embed without image
        await interaction.followup.send(embed=embed)


# ── /predict ──────────────────────────────────────────────────────────────────

_OUTCOME_EMOJI = {"Home Win": "🏠", "Draw": "🟡", "Away Win": "✈️"}

@tree.command(name="predict", description="Predicted scores for upcoming matches in a league")
@app_commands.describe(league="Which league to predict")
@app_commands.choices(league=[
    app_commands.Choice(name="🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League", value="premier_league"),
    app_commands.Choice(name="🇪🇸 La Liga",        value="la_liga"),
    app_commands.Choice(name="🇮🇹 Serie A",         value="serie_a"),
    app_commands.Choice(name="🇩🇪 Bundesliga",      value="bundesliga"),
    app_commands.Choice(name="🇫🇷 Ligue 1",         value="ligue_1"),
    app_commands.Choice(name="🇨🇴 Liga BetPlay",    value="liga_betplay"),
])
async def cmd_predict(interaction: discord.Interaction, league: str = "premier_league"):
    await interaction.response.defer()
    loop = asyncio.get_event_loop()

    result = await loop.run_in_executor(None, get_predictions, league)
    league_info  = result.get("league") or LEAGUES.get(league, {})
    predictions  = result.get("predictions", [])
    fetch_error  = result.get("error")

    if not predictions:
        await interaction.followup.send(
            embed=discord.Embed(
                title=f"{league_info.get('flag','')} {league_info.get('name','League')} — No fixtures",
                description=fetch_error or "No upcoming fixtures found.",
                colour=C_DRAW,
            )
        )
        return

    embed = discord.Embed(
        title=f"{league_info.get('flag','')} {league_info.get('name','League')} — Predicted Scores",
        colour=0xa78bfa,
        description="Poisson model · team form from last 8 matches",
    )

    for p in predictions[:8]:
        oe = _OUTCOME_EMOJI.get(p["outcome"], "")
        bar = f"🏠 `{p['p_home']:>4.0f}%`  🟡 `{p['p_draw']:>4.0f}%`  ✈️ `{p['p_away']:>4.0f}%`"
        embed.add_field(
            name=f"📅 {p['date']}  ·  {p['home']} vs {p['away']}",
            value=(
                f"**{p['scoreline']}** — {oe} {p['outcome']} ({p['confidence']}%)\n"
                f"{bar}\n"
                f"xG: `{p['xg_home']}` — `{p['xg_away']}`"
            ),
            inline=False,
        )

    embed.set_footer(text="Predictions are probabilistic estimates, not guarantees · Data: FotMob")
    await interaction.followup.send(embed=embed)


# ── /fotmob_help ──────────────────────────────────────────────────────────────

@tree.command(name="fotmob_help", description="List all FotMob bot commands")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚽  FotMob Bot — Commands",
        colour=C_GOLD,
        description="All commands search the local DB first, then scrape FotMob live if needed.",
    )
    cmds = [
        ("🧑  /player `<name>`",                    "Full profile — bio, form strip, last 5 matches, G+A"),
        ("📊  /stats `<name>`",                     "Season stats with xG/xA, rating, and progress bars"),
        ("🗓️  /matches `<name>` `[count]`",         "Match log with scores, G, A, rating, MOTM ⭐"),
        ("🏟️  /match `<name>` `[number]`",          "Lineup + key events for a specific match (default: latest)"),
        ("📋  /career `<name>`",                    "Club-by-club career with totals"),
        ("⚔️  /compare `<player1>` `<player2>`",   "Head-to-head with 🥇/🥈 per category"),
        ("⚡  /predict `<league>`",                 "Predicted scores for upcoming matches (Poisson model)"),
    ]
    for name, desc in cmds:
        embed.add_field(name=name, value=desc, inline=False)
    embed.set_footer(text="Data from FotMob  ·  Cached in PostgreSQL")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    bot.run(token)
