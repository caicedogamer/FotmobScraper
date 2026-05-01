"""
scraper.py
----------
Scrapes player stats from FotMob using the Next.js _next/data JSON endpoint.

FotMob embeds a buildId in every HTML page that changes on each deployment.
This script fetches the player page first to extract the current buildId,
then constructs the correct JSON URL.

Usage:
    python scraper.py                          # Bruno Fernandes (default)
    python scraper.py 422685 bruno-fernandes
    python scraper.py 961995 erling-haaland
    python scraper.py 39381 cristiano-ronaldo --raw

Requirements:
    pip install requests
"""

import argparse
import json
import logging
import re
import sys
from typing import Optional

import requests
from fotmob import fetch_backend as _fb

_logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

BASE_URL   = "https://www.fotmob.com"
SEARCH_API = "https://apigw.fotmob.com/searchapi/suggest"

# Do NOT include Accept-Encoding — advertising Brotli support causes FotMob
# to respond with br-encoded content that requests cannot decode.
SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

JSON_HEADERS = {
    "User-Agent": SESSION_HEADERS["User-Agent"],
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


# ──────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────

def name_to_slug(name: str) -> str:
    """'Erling Haaland' -> 'erling-haaland'"""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return re.sub(r"-+", "-", slug)


def search_players(term: str) -> list[dict]:
    """
    Query FotMob's suggest API and return a list of player dicts:
      [{id, name, slug, team}, ...]
    Coaches are excluded.
    """
    resp = requests.get(
        SEARCH_API,
        params={"term": term, "lang": "en"},
        headers={"User-Agent": SESSION_HEADERS["User-Agent"], "Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    results = []
    for group in resp.json().get("squadMemberSuggest", []):
        for opt in group.get("options", []):
            payload = opt.get("payload", {})
            if payload.get("isCoach"):
                continue
            raw_name = opt.get("text", "").split("|")[0]
            results.append({
                "id":   int(payload["id"]),
                "name": raw_name,
                "slug": name_to_slug(raw_name),
                "team": payload.get("teamName", ""),
            })
    return results


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(SESSION_HEADERS)
    try:
        session.get(BASE_URL, timeout=15)
    except requests.RequestException:
        pass
    return session


def get_build_id(
    session: requests.Session,
    player_id: int,
    slug: str,
    engine: str = "requests",
) -> str:
    url = f"{BASE_URL}/en/players/{player_id}/{slug}"
    html = _fb.fetch_text(url, timeout=15, engine=engine, session=session)
    match = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    if not match:
        raise ValueError(
            "buildId not found in page HTML. "
            "FotMob may have changed their structure or you're being blocked."
        )
    return match.group(1)


def fetch_player_json(
    session: requests.Session,
    player_id: int,
    slug: str,
    engine: str = "requests",
) -> dict:
    """Resolve buildId then fetch the _next/data JSON for a player."""
    print(f"→ Resolving buildId for player {player_id} ({slug})...")
    build_id = get_build_id(session, player_id, slug, engine=engine)
    print(f"  buildId: {build_id}")

    json_url = (
        f"{BASE_URL}/_next/data/{build_id}/en/players/{player_id}/{slug}.json"
        f"?lng=en&id={player_id}&slug={slug}"
    )
    print("→ Fetching JSON data...")
    # JSON endpoint always uses the requests session (needs warmup cookies).
    resp = session.get(json_url, headers=JSON_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_match_json(
    session: requests.Session,
    match_url: str,
    engine: str = "requests",
) -> dict:
    """
    Given a FotMob match page URL (relative or absolute), resolve buildId
    and return the _next/data JSON for that match.

    Supported URL formats (FotMob has used both over time):
      /matches/chelsea-vs-man-city/2d55kw#4813688
      /match/4317858/overview/man-city-vs-arsenal
      https://www.fotmob.com/matches/...
    """
    # Strip fragment (#matchId) — kept separately if present
    fragment = ""
    if "#" in match_url:
        match_url, fragment = match_url.split("#", 1)

    # Normalise to absolute, strip trailing slash
    if match_url.startswith("/"):
        page_url = BASE_URL + match_url.rstrip("/")
    else:
        page_url = match_url.rstrip("/")

    print(f"→ Fetching match page: {page_url}")
    # HTML page fetch can use the engine (Scrapling fallback if blocked).
    html = _fb.fetch_text(page_url, timeout=15, engine=engine, session=session)

    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    if not m:
        raise ValueError("buildId not found on match page")
    build_id = m.group(1)
    print(f"  buildId: {build_id}")

    # Parse path to build the _next/data URL
    # Strip base URL to get just the path
    path  = page_url.replace(BASE_URL, "").lstrip("/")
    parts = path.split("/")  # e.g. ['matches', 'chelsea-vs-man-city', '2d55kw']

    if parts[0] == "matches":
        # New-style: /matches/{fixture-slug}/{code}  — match ID in fragment
        if not fragment:
            # Try to extract matchId from the page HTML as a fallback
            mid_m = re.search(r'"matchId"\s*:\s*(\d+)', html)
            fragment = mid_m.group(1) if mid_m else parts[-1]
        match_id = fragment
        # The Next.js JSON path mirrors the page path exactly
        json_path = "/".join(parts)   # matches/chelsea-vs-man-city/2d55kw
        json_url = (
            f"{BASE_URL}/_next/data/{build_id}/en/{json_path}.json"
            f"?matchId={match_id}"
        )

    elif parts[0] == "match":
        # Old-style: /match/{matchId}/{tab}/{slug}
        match_id = parts[1]
        tab      = parts[2] if len(parts) > 2 else "overview"
        slug     = parts[3] if len(parts) > 3 else match_id
        json_url = (
            f"{BASE_URL}/_next/data/{build_id}/en/match/{match_id}/{tab}/{slug}.json"
            f"?matchId={match_id}"
        )

    else:
        raise ValueError(f"Unexpected match URL format: {page_url}")

    print(f"→ Fetching match JSON: {json_url}")
    resp = session.get(json_url, headers=JSON_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_match(data: dict) -> dict:
    """Extract match details, lineups, and key events from raw match JSON."""
    pp      = data.get("pageProps", {})
    general = pp.get("general", {})
    header  = pp.get("header",  {})
    content = pp.get("content", {})

    home_team_info = general.get("homeTeam", {})
    away_team_info = general.get("awayTeam", {})
    lineup_data    = content.get("lineup", {})

    # Score lives in header.status.scoreStr e.g. "1 - 2"
    score_str = (header.get("status") or {}).get("scoreStr", "")
    score = score_str.replace(" - ", "–") if score_str else "?–?"

    match_date = (general.get("matchTimeUTCDate") or "")[:10]
    league     = general.get("leagueName") or ""

    # Build per-player event lookups from header.events
    h_events = header.get("events") or {}
    scorer_ids   = {}   # player_id -> goal count
    red_card_ids = set()

    for goal_dict in [h_events.get("homeTeamGoals") or {}, h_events.get("awayTeamGoals") or {}]:
        for goals in goal_dict.values():
            for g in goals:
                pid = (g.get("player") or {}).get("id")
                if pid:
                    scorer_ids[pid] = scorer_ids.get(pid, 0) + 1

    for card_dict in [h_events.get("homeTeamRedCards") or {}, h_events.get("awayTeamRedCards") or {}]:
        for cards in card_dict.values():
            for c in cards:
                pid = (c.get("player") or {}).get("id")
                if pid:
                    red_card_ids.add(pid)

    # Yellow cards from matchFacts events
    yellow_card_ids = set()
    for ev in (content.get("matchFacts") or {}).get("events", {}).get("events") or []:
        if ev.get("type") == "Card" and ev.get("card", "").lower() not in ("red",):
            pid = ev.get("playerId") or (ev.get("player") or {}).get("id")
            if pid:
                yellow_card_ids.add(pid)

    def _parse_side(side_key: str) -> list[dict]:
        side = lineup_data.get(side_key) or {}
        players = []

        for p in side.get("starters") or []:
            perf = p.get("performance") or {}
            vl   = p.get("verticalLayout") or {}
            pid  = p.get("id")
            players.append({
                "id":      pid,
                "name":    p.get("name") or "?",
                "shirt":   p.get("shirtNumber"),
                "starter": True,
                "rating":  perf.get("rating"),
                "x_norm":  vl.get("x", 0.5),
                "y_norm":  vl.get("y", 0.5),
                "goals":   scorer_ids.get(pid, 0),
                "assists": 0,
                "yellow":  pid in yellow_card_ids,
                "red":     pid in red_card_ids,
                "motm":    bool(perf.get("isMotm")),
                "subbed_off": None,
                "subbed_on":  None,
            })

        for p in side.get("subs") or []:
            perf      = p.get("performance") or {}
            sub_evs   = perf.get("substitutionEvents") or []
            sub_in    = next((e.get("time") for e in sub_evs if e.get("type") == "subIn"),  None)
            sub_out   = next((e.get("time") for e in sub_evs if e.get("type") == "subOut"), None)
            pid       = p.get("id")
            players.append({
                "id":      pid,
                "name":    p.get("name") or "?",
                "shirt":   p.get("shirtNumber"),
                "starter": False,
                "rating":  perf.get("rating"),
                "x_norm":  0.5,
                "y_norm":  0.5,
                "goals":   scorer_ids.get(pid, 0),
                "assists": 0,
                "yellow":  pid in yellow_card_ids,
                "red":     pid in red_card_ids,
                "motm":    False,
                "subbed_on":  sub_in,
                "subbed_off": sub_out,
            })

        return players

    home_lineup = _parse_side("homeTeam")
    away_lineup = _parse_side("awayTeam")

    home_formation = (lineup_data.get("homeTeam") or {}).get("formation") or ""
    away_formation = (lineup_data.get("awayTeam") or {}).get("formation") or ""

    # Key events ordered by minute
    events = []
    for is_home, goal_dict in [(True,  h_events.get("homeTeamGoals") or {}),
                                (False, h_events.get("awayTeamGoals") or {})]:
        for goals in goal_dict.values():
            for g in goals:
                events.append({
                    "type":   "OwnGoal" if g.get("ownGoal") else "Goal",
                    "minute": g.get("time"),
                    "player": g.get("fullName") or "",
                    "team":   home_team_info.get("name") if is_home else away_team_info.get("name"),
                    "detail": g.get("assistStr") or "",
                })

    for is_home, card_dict in [(True,  h_events.get("homeTeamRedCards") or {}),
                                (False, h_events.get("awayTeamRedCards") or {})]:
        for cards in card_dict.values():
            for c in cards:
                events.append({
                    "type":   "Card",
                    "minute": c.get("time"),
                    "player": c.get("fullName") or "",
                    "team":   home_team_info.get("name") if is_home else away_team_info.get("name"),
                    "detail": "Red Card",
                })

    events.sort(key=lambda e: e.get("minute") or 0)

    return {
        "match_id":       general.get("matchId"),
        "date":           match_date,
        "league":         league,
        "venue":          "",
        "home_team":      home_team_info.get("name") or "?",
        "away_team":      away_team_info.get("name") or "?",
        "home_id":        home_team_info.get("id"),
        "away_id":        away_team_info.get("id"),
        "score":          score,
        "home_formation": home_formation,
        "away_formation": away_formation,
        "home_lineup":    home_lineup,
        "away_lineup":    away_lineup,
        "events":         events,
    }


# ──────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────

def safe_get(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is None:
            return default
    return d


def _player_info_value(info_list: list, title: str):
    for item in info_list:
        if item.get("title") == title:
            v = item.get("value", {})
            return v.get("numberValue") if v.get("numberValue") is not None else v.get("fallback")
    return None


def _match_result(m: dict) -> str:
    home, away = m.get("homeScore"), m.get("awayScore")
    if home is None or away is None:
        return "?"
    team_score = home if m.get("isHomeTeam") else away
    opp_score  = away if m.get("isHomeTeam") else home
    if team_score > opp_score: return "W"
    if team_score < opp_score: return "L"
    return "D"


def parse_player(data: dict) -> dict:
    """Extract a clean player dict from the raw FotMob _next/data response."""
    d = data.get("pageProps", {}).get("data", {})
    info_list = d.get("playerInformation") or []

    # Season stats (current league)
    parsed_stats = {}
    for stat in (d.get("mainLeague") or {}).get("stats") or []:
        label = stat.get("title") or stat.get("localizedTitleId")
        if label is not None:
            parsed_stats[label] = stat.get("value")

    # Career (club-by-club)
    parsed_career = []
    career_items = (d.get("careerHistory") or {}).get("careerItems") or {}
    for entry in (career_items.get("senior") or {}).get("teamEntries") or []:
        parsed_career.append({
            "team":        entry.get("team"),
            "start":       (entry.get("startDate") or "")[:10],
            "end":         (entry.get("endDate") or "present")[:10] or "present",
            "appearances": entry.get("appearances"),
            "goals":       entry.get("goals"),
            "assists":     entry.get("assists"),
        })

    # Recent matches (skip bench appearances)
    parsed_matches = []
    for m in d.get("recentMatches") or []:
        if m.get("onBench"):
            continue
        is_home  = m.get("isHomeTeam")
        opponent = m.get("opponentTeamName")
        fixture  = (
            f"{m.get('teamName')} vs {opponent}" if is_home
            else f"{opponent} vs {m.get('teamName')}"
        )
        parsed_matches.append({
            "date":    (m.get("matchDate") or {}).get("utcTime", "")[:10],
            "fixture": fixture,
            "league":  m.get("leagueName"),
            "score":   f"{m.get('homeScore')}–{m.get('awayScore')}",
            "result":  _match_result(m),
            "mins":    m.get("minutesPlayed"),
            "goals":   m.get("goals"),
            "assists": m.get("assists"),
            "rating":  (m.get("ratingProps") or {}).get("rating"),
            "motm":    m.get("playerOfTheMatch", False),
            "url":     m.get("matchPageUrl"),
        })

    player_id = d.get("id")
    return {
        "id":            player_id,
        "slug":          safe_get(d, "meta", "seopath", default="").rstrip("/").split("/")[-1] or str(player_id),
        "name":          d.get("name"),
        "image_url":     f"https://images.fotmob.com/image_resources/playerimages/{player_id}.png",
        "position":      safe_get(d, "positionDescription", "primaryPosition", "label"),
        "nationality":   _player_info_value(info_list, "Country"),
        "age":           _player_info_value(info_list, "Age"),
        "club":          safe_get(d, "primaryTeam", "teamName"),
        "jersey_number": _player_info_value(info_list, "Shirt"),
        "season_stats":  parsed_stats,
        "career":        parsed_career,
        "matches":       parsed_matches,
    }


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def _print_summary(player: dict):
    print("\n" + "═" * 48)
    print(f"  {player.get('name', 'Unknown')}")
    print("═" * 48)
    print(f"  Club:        {player.get('club', 'N/A')}")
    print(f"  Position:    {player.get('position', 'N/A')}")
    print(f"  Nationality: {player.get('nationality', 'N/A')}")
    print(f"  Age:         {player.get('age', 'N/A')}")
    print(f"  Jersey:      #{player.get('jersey_number', 'N/A')}")

    stats = player.get("season_stats", {})
    if stats:
        print("\n  ── Season Stats ──")
        for k, v in stats.items():
            print(f"    {k:<30} {v}")

    career = player.get("career", [])
    if career:
        print(f"\n  ── Career ({len(career)} clubs) ──")
        print(f"  {'Team':<25} {'Start':<12} {'End':<12} {'Apps':>5} {'G':>4} {'A':>4}")
        print("  " + "-" * 62)
        for c in career:
            print(
                f"  {str(c.get('team') or ''):<25}"
                f" {str(c.get('start') or ''):<12}"
                f" {str(c.get('end') or ''):<12}"
                f" {str(c.get('appearances') or ''):>5}"
                f" {str(c.get('goals') or ''):>4}"
                f" {str(c.get('assists') or ''):>4}"
            )
    print("═" * 48)


def main():
    parser = argparse.ArgumentParser(description="FotMob player scraper")
    parser.add_argument("player_id", nargs="?", type=int, default=422685)
    parser.add_argument("slug",      nargs="?", type=str, default="bruno-fernandes")
    parser.add_argument("--raw",    action="store_true", help="Print raw JSON response")
    parser.add_argument(
        "--engine", default="requests",
        choices=["requests", "scrapling", "auto"],
        help="Fetch backend: requests (default), scrapling, or auto (requests with Scrapling fallback)",
    )
    args = parser.parse_args()

    session = make_session()
    try:
        raw_data = fetch_player_json(session, args.player_id, args.slug, engine=args.engine)
    except requests.HTTPError as e:
        print(f"\n✗ HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"\n✗ Network error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.raw:
        print(json.dumps(raw_data, indent=2, ensure_ascii=False))
        return

    _print_summary(parse_player(raw_data))


if __name__ == "__main__":
    main()
