"""Generate original card ratings from scraped FotMob player data."""

from __future__ import annotations

import argparse
import math
import re
import sys
from statistics import median

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.cards import rarity_for_rating
from fotmob.game.db import init_game_db

FORMULA_VERSION = "fotmob_percentile_v1"

STAT_ALIASES = {
    "minutes": ["minutes played", "mins", "minutes"],
    "rating": ["rating", "fotmob rating", "average rating"],
    "goals": ["goals", "goal"],
    "assists": ["assists", "assist"],
    "xg": ["expected goals (xg)", "xg", "expected goals"],
    "xa": ["expected assists (xa)", "xa", "expected assists"],
    "shots": ["shots", "shots on target"],
    "chances": ["chances created", "big chances created", "key passes"],
    "passes": ["accurate passes", "pass accuracy", "successful passes"],
    "tackles": ["tackles won", "tackles"],
    "interceptions": ["interceptions"],
    "clearances": ["clearances"],
    "aerials": ["aerial duels won", "duels won"],
    "saves": ["saves", "save percentage", "goals prevented"],
    "clean_sheets": ["clean sheets", "clean sheet"],
}

POSITION_GROUPS = {
    "GK": ["goalkeeper", "keeper", "gk"],
    "DEF": ["defender", "centre-back", "center-back", "back", "cb", "lb", "rb"],
    "MID": ["midfielder", "midfield", "cm", "dm", "am"],
    "ATT": ["forward", "striker", "winger", "attack", "st", "lw", "rw", "cf"],
}

WEIGHTS = {
    "ATT": {
        "rating": 0.30, "goals_p90": 0.24, "assists_p90": 0.14,
        "xg_p90": 0.13, "xa_p90": 0.09, "shots_p90": 0.05, "minutes": 0.05,
    },
    "MID": {
        "rating": 0.30, "assists_p90": 0.15, "xa_p90": 0.13,
        "chances_p90": 0.13, "passes_p90": 0.09, "tackles_p90": 0.08,
        "interceptions_p90": 0.05, "minutes": 0.07,
    },
    "DEF": {
        "rating": 0.30, "tackles_p90": 0.15, "interceptions_p90": 0.13,
        "clearances_p90": 0.14, "aerials_p90": 0.10, "clean_sheets_p90": 0.08,
        "minutes": 0.10,
    },
    "GK": {
        "rating": 0.34, "saves_p90": 0.22, "clean_sheets_p90": 0.18,
        "minutes": 0.16, "passes_p90": 0.05, "goals_p90": 0.05,
    },
}


def _number(value) -> float:
    if value is None:
        return 0.0
    text = str(value).replace(",", "").replace("%", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").lower()).strip()


def _pick_stat(stats: dict[str, str], key: str) -> float:
    labels = {_norm_label(k): v for k, v in stats.items()}
    for alias in STAT_ALIASES[key]:
        alias_norm = _norm_label(alias)
        for label, value in labels.items():
            if label == alias_norm or alias_norm in label:
                return _number(value)
    return 0.0


def position_group(position: str) -> str:
    text = _norm_label(position)
    for group, needles in POSITION_GROUPS.items():
        if any(needle in text for needle in needles):
            return group
    return "MID"


def _percentiles(values: list[float]) -> dict[float, float]:
    ordered = sorted(values)
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0]: 50.0}
    return {
        value: idx / (len(ordered) - 1) * 100
        for idx, value in enumerate(ordered)
    }


def _pct(value: float, mapping: dict[float, float]) -> float:
    if not mapping:
        return 50.0
    if value in mapping:
        return mapping[value]
    keys = sorted(mapping)
    lower = max((k for k in keys if k <= value), default=keys[0])
    upper = min((k for k in keys if k >= value), default=keys[-1])
    if lower == upper:
        return mapping[lower]
    span = upper - lower
    return mapping[lower] + (value - lower) / span * (mapping[upper] - mapping[lower])


def _minute_cap(minutes: float) -> int:
    if minutes < 300:
        return 74
    if minutes < 900:
        return 82
    if minutes < 1500:
        return 87
    return 94


def _rating_from_score(score: float, minutes: float) -> int:
    rating = round(50 + max(0, min(100, score)) * 0.44)
    return max(50, min(_minute_cap(minutes), rating))


def _performance_floor(fotmob_rating: float, minutes: float) -> int:
    """Stabilise ratings when the local comparison pool is small or sparse."""
    if minutes < 300:
        return 50
    if fotmob_rating >= 7.8:
        return 87
    if fotmob_rating >= 7.5:
        return 84
    if fotmob_rating >= 7.25:
        return 81
    if fotmob_rating >= 7.0:
        return 77
    if fotmob_rating >= 6.75:
        return 73
    if fotmob_rating >= 6.5:
        return 68
    return 50


def _feature_row(player: dict) -> dict:
    stats = player.get("season_stats") or {}
    matches = player.get("matches", [])
    match_minutes = sum((m.get("mins") or 0) for m in matches)
    match_goals = sum((m.get("goals") or 0) for m in matches)
    match_assists = sum((m.get("assists") or 0) for m in matches)
    match_ratings = [_number(m.get("rating")) for m in matches if _number(m.get("rating"))]

    minutes = _pick_stat(stats, "minutes")
    if not minutes:
        minutes = match_minutes
    per90 = max(minutes / 90, 1)
    goals = _pick_stat(stats, "goals") or match_goals
    assists = _pick_stat(stats, "assists") or match_assists
    rating = _pick_stat(stats, "rating")
    if not rating:
        rating = median(match_ratings) if match_ratings else 6.4

    row = {
        "player": player,
        "group": position_group(player.get("position") or ""),
        "minutes": minutes,
        "rating": rating,
        "goals_p90": goals / per90,
        "assists_p90": assists / per90,
        "xg_p90": _pick_stat(stats, "xg") / per90,
        "xa_p90": _pick_stat(stats, "xa") / per90,
        "shots_p90": _pick_stat(stats, "shots") / per90,
        "chances_p90": _pick_stat(stats, "chances") / per90,
        "passes_p90": _pick_stat(stats, "passes") / per90,
        "tackles_p90": _pick_stat(stats, "tackles") / per90,
        "interceptions_p90": _pick_stat(stats, "interceptions") / per90,
        "clearances_p90": _pick_stat(stats, "clearances") / per90,
        "aerials_p90": _pick_stat(stats, "aerials") / per90,
        "saves_p90": _pick_stat(stats, "saves") / per90,
        "clean_sheets_p90": _pick_stat(stats, "clean_sheets") / per90,
    }
    return row


def _load_players(limit: int | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, slug, name, image_url, position, nationality, club
                FROM players
                ORDER BY name
                LIMIT COALESCE(%s, 2147483647)
            """, (limit,))
            players = [dict(r) for r in cur.fetchall()]
            for player in players:
                cur.execute(
                    "SELECT label, value FROM season_stats WHERE player_id = %s",
                    (player["id"],),
                )
                player["season_stats"] = {r["label"]: r["value"] for r in cur.fetchall()}
                cur.execute(
                    "SELECT mins, goals, assists, rating FROM matches WHERE player_id = %s",
                    (player["id"],),
                )
                player["matches"] = [dict(r) for r in cur.fetchall()]
    return players


def rate_players(players: list[dict], min_minutes: int = 0) -> list[dict]:
    rows = [_feature_row(p) for p in players]
    if min_minutes:
        rows = [r for r in rows if r["minutes"] >= min_minutes]

    percentile_maps: dict[str, dict[str, dict[float, float]]] = {}
    for group in WEIGHTS:
        group_rows = [r for r in rows if r["group"] == group]
        percentile_maps[group] = {}
        for feature in WEIGHTS[group]:
            percentile_maps[group][feature] = _percentiles([r[feature] for r in group_rows])

    cards = []
    for row in rows:
        group = row["group"]
        score = 0.0
        for feature, weight in WEIGHTS[group].items():
            score += _pct(row[feature], percentile_maps[group].get(feature, {})) * weight
        rating = max(
            _rating_from_score(score, row["minutes"]),
            min(_minute_cap(row["minutes"]), _performance_floor(row["rating"], row["minutes"])),
        )
        player = row["player"]
        cards.append({
            "player_source_id": player["id"],
            "name": player.get("name") or player.get("slug") or str(player["id"]),
            "club": player.get("club"),
            "nationality": player.get("nationality"),
            "position": player.get("position"),
            "rating": rating,
            "rarity": rarity_for_rating(rating),
            "image_url": player.get("image_url"),
            "card_type": "generated",
            "rating_score": round(score, 4),
            "rating_formula_version": FORMULA_VERSION,
            "minutes": row["minutes"],
            "position_group": group,
        })
    return sorted(cards, key=lambda c: (c["rating"], c["name"]), reverse=True)


def upsert_generated_cards(cards: list[dict]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            for c in cards:
                cur.execute("""
                    INSERT INTO game_player_cards
                        (player_source_id, name, club, nationality, position,
                         rating, rarity, image_url, card_type, is_active,
                         rating_formula_version, rating_score, rating_updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,NOW())
                    ON CONFLICT (name, club, card_type) DO UPDATE SET
                        player_source_id = EXCLUDED.player_source_id,
                        nationality = EXCLUDED.nationality,
                        position = EXCLUDED.position,
                        rating = EXCLUDED.rating,
                        rarity = EXCLUDED.rarity,
                        image_url = EXCLUDED.image_url,
                        rating_formula_version = EXCLUDED.rating_formula_version,
                        rating_score = EXCLUDED.rating_score,
                        rating_updated_at = NOW(),
                        is_active = TRUE
                """, (
                    c["player_source_id"], c["name"], c["club"], c["nationality"],
                    c["position"], c["rating"], c["rarity"], c["image_url"],
                    c["card_type"], c["rating_formula_version"], c["rating_score"],
                ))
    return len(cards)


def generate_from_db(limit: int | None = None, min_minutes: int = 0, dry_run: bool = False) -> dict:
    init_game_db()
    players = _load_players(limit=limit)
    cards = rate_players(players, min_minutes=min_minutes)
    written = 0 if dry_run else upsert_generated_cards(cards)
    return {"loaded": len(players), "generated": len(cards), "written": written, "cards": cards}


def main():
    parser = argparse.ArgumentParser(description="Generate game cards from scraped FotMob players")
    parser.add_argument("--limit", type=int, default=None, help="Max scraped players to load")
    parser.add_argument("--min-minutes", type=int, default=0, help="Ignore players below this minutes total")
    parser.add_argument("--dry-run", action="store_true", help="Print top generated cards without writing")
    args = parser.parse_args()

    result = generate_from_db(limit=args.limit, min_minutes=args.min_minutes, dry_run=args.dry_run)
    print(f"Loaded {result['loaded']} scraped players")
    print(f"Generated {result['generated']} cards")
    if not args.dry_run:
        print(f"Upserted {result['written']} cards into game_player_cards")
    print("\nTop generated cards:")
    for card in result["cards"][:20]:
        print(
            f"  {card['rating']:>2} {card['name']:<28} "
            f"{card['rarity']:<10} {card['position_group']} {card['minutes']:.0f} mins"
        )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
