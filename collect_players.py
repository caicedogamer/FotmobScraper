"""
collect_players.py
------------------
Collect real player names from FotMob league/team squad pages.

Examples:
    python collect_players.py --default-leagues
    python collect_players.py --league mls 130 mls
    python collect_players.py --league premier_league 47 premier-league --append
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from fotmob.scraper import BASE_URL, SESSION_HEADERS, make_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_LEAGUES = [
    ("premier_league", 47, "premier-league"),
    ("la_liga", 87, "laliga"),
    ("serie_a", 55, "serie-a"),
    ("bundesliga", 54, "bundesliga"),
    ("ligue_1", 53, "ligue-1"),
    ("mls", 130, "mls"),
    ("liga_mx", 230, "liga-mx"),
    ("brasileirao", 268, "serie"),
    ("liga_portugal", 61, "liga-portugal"),
    ("super_lig", 71, "super-lig"),
]


def _page_props(session, url: str) -> dict:
    html = session.get(url, headers=SESSION_HEADERS, timeout=25).text
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise RuntimeError(f"__NEXT_DATA__ not found: {url}")
    return json.loads(match.group(1))["props"]["pageProps"]


def _league_teams(session, league_id: int, slug: str) -> tuple[str, list[dict]]:
    url = f"{BASE_URL}/leagues/{league_id}/overview/{slug}"
    pp = _page_props(session, url)
    details = pp.get("details") or {}
    league_name = details.get("name") or f"league-{league_id}"
    teams = []
    for table in pp.get("table") or []:
        table_obj = ((table.get("data") or {}).get("table") or {})
        for row in table_obj.get("all") or []:
            if row.get("id") and row.get("pageUrl"):
                teams.append({
                    "id": int(row["id"]),
                    "name": row.get("name") or row.get("shortName") or str(row["id"]),
                    "pageUrl": row["pageUrl"],
                })
    seen = set()
    deduped = []
    for team in teams:
        if team["id"] not in seen:
            seen.add(team["id"])
            deduped.append(team)
    return league_name, deduped


def _team_players(session, team: dict, league_key: str, league_name: str) -> list[dict]:
    url = BASE_URL + team["pageUrl"] if team["pageUrl"].startswith("/") else team["pageUrl"]
    pp = _page_props(session, url)
    team_data = (pp.get("fallback") or {}).get(f"team-{team['id']}") or pp
    squad_groups = ((team_data.get("squad") or {}).get("squad") or [])
    out = []
    for group in squad_groups:
        if group.get("title") == "coach":
            continue
        for member in group.get("members") or []:
            role = (member.get("role") or {}).get("key", "")
            if "coach" in role:
                continue
            player_id = member.get("id")
            name = (member.get("name") or "").strip()
            if not player_id or not name:
                continue
            out.append({
                "id": str(player_id),
                "name": name,
                "team": team["name"],
                "position": member.get("positionIdsDesc") or (member.get("role") or {}).get("fallback") or "",
                "country": member.get("cname") or "",
                "league_key": league_key,
                "league": league_name,
            })
    return out


def _read_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = {}
        for row in reader:
            if row.get("id"):
                rows[str(row["id"])] = row
        return rows


def _write_outputs(players: dict[str, dict], names_path: Path, meta_path: Path):
    rows = sorted(
        players.values(),
        key=lambda p: ((p.get("name") or "").split()[-1].lower(), (p.get("name") or "").lower()),
    )
    names_path.write_text(
        "\n".join(row["name"] for row in rows if row.get("name")) + "\n",
        encoding="utf-8",
    )
    fields = ["id", "name", "team", "position", "country", "league_key", "league"]
    with meta_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect(leagues: list[tuple[str, int, str]], append: bool, delay: float) -> dict[str, dict]:
    session = make_session()
    players = _read_existing(Path("players_with_meta.tsv")) if append else {}
    failures = []

    for league_key, league_id, slug in leagues:
        try:
            league_name, teams = _league_teams(session, league_id, slug)
            print(f"{league_key}: {league_name} - {len(teams)} teams")
        except Exception as exc:
            print(f"WARN league {league_key}: {exc}")
            continue
        time.sleep(delay)

        for idx, team in enumerate(teams, 1):
            try:
                squad = _team_players(session, team, league_key, league_name)
                print(f"  {idx:>2}/{len(teams)} {team['name']}: {len(squad)} players")
                for player in squad:
                    if player["id"] not in players:
                        players[player["id"]] = player
                    else:
                        existing = players[player["id"]]
                        if league_key not in (existing.get("league_key") or ""):
                            existing["league_key"] = f"{existing.get('league_key','')},{league_key}".strip(",")
                        if league_name not in (existing.get("league") or ""):
                            existing["league"] = f"{existing.get('league','')},{league_name}".strip(",")
            except Exception as exc:
                failures.append((league_key, team["name"], str(exc)))
                print(f"  WARN team {team['name']}: {exc}")
            time.sleep(delay)

    _write_outputs(players, Path("players.txt"), Path("players_with_meta.tsv"))
    print(f"\nWrote {len(players)} unique real players")
    if failures:
        print(f"Failures: {len(failures)}")
        for league_key, team, err in failures[:20]:
            print(f"  {league_key} / {team}: {err}")
    return players


def main():
    parser = argparse.ArgumentParser(description="Collect real players from FotMob squads")
    parser.add_argument("--default-leagues", action="store_true", help="Collect the built-in league list")
    parser.add_argument("--append", action="store_true", help="Merge with existing players_with_meta.tsv")
    parser.add_argument("--delay", type=float, default=0.25, help="Delay between team requests")
    parser.add_argument(
        "--league",
        nargs=3,
        action="append",
        metavar=("KEY", "ID", "SLUG"),
        help="Add one FotMob league, e.g. --league mls 130 mls",
    )
    args = parser.parse_args()

    leagues = []
    if args.default_leagues:
        leagues.extend(DEFAULT_LEAGUES)
    if args.league:
        leagues.extend((key, int(league_id), slug) for key, league_id, slug in args.league)
    if not leagues:
        parser.error("Use --default-leagues or one or more --league KEY ID SLUG entries")

    collect(leagues, append=args.append, delay=args.delay)


if __name__ == "__main__":
    main()
