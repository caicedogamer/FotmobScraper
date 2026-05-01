"""
db.py
-----
PostgreSQL persistence for FotMob player data.
Connection settings are read from .env (see .env for defaults).

Schema
------
players      — one row per player (upserted on refresh)
season_stats — key/value stats for the player's current league season
career       — one row per club in the player's career
matches      — one row per recent match
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=os.getenv("PG_PORT", "5432"),
        dbname=os.getenv("PG_DB", "fotmob"),
        user=os.getenv("PG_USER", "postgres"),
        password=os.getenv("PG_PASSWORD", "postgres"),
    )
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id            INTEGER PRIMARY KEY,
                    slug          TEXT    NOT NULL,
                    name          TEXT,
                    image_url     TEXT,
                    position      TEXT,
                    nationality   TEXT,
                    age           INTEGER,
                    club          TEXT,
                    jersey_number INTEGER,
                    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS season_stats (
                    id        SERIAL PRIMARY KEY,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    label     TEXT    NOT NULL,
                    value     TEXT
                );

                CREATE TABLE IF NOT EXISTS career (
                    id          SERIAL PRIMARY KEY,
                    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    team        TEXT,
                    start_date  TEXT,
                    end_date    TEXT,
                    appearances TEXT,
                    goals       TEXT,
                    assists     TEXT
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id          SERIAL PRIMARY KEY,
                    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    match_date  TEXT,
                    fixture     TEXT,
                    league      TEXT,
                    score       TEXT,
                    result      TEXT,
                    mins        INTEGER,
                    goals       INTEGER,
                    assists     INTEGER,
                    rating      TEXT,
                    motm        BOOLEAN DEFAULT FALSE,
                    url         TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_season_stats_player
                    ON season_stats(player_id);
                CREATE INDEX IF NOT EXISTS idx_career_player
                    ON career(player_id);
                CREATE INDEX IF NOT EXISTS idx_matches_player
                    ON matches(player_id);
                CREATE INDEX IF NOT EXISTS idx_matches_player_date
                    ON matches(player_id, match_date DESC);
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS imported_matches (
                    id               SERIAL      PRIMARY KEY,
                    source           TEXT        NOT NULL,
                    source_match_id  TEXT        NOT NULL,
                    match_url        TEXT,
                    match_date       TEXT,
                    league           TEXT,
                    home_team        TEXT,
                    away_team        TEXT,
                    home_id          TEXT,
                    away_id          TEXT,
                    score            TEXT,
                    home_formation   TEXT,
                    away_formation   TEXT,
                    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (source, source_match_id)
                );

                CREATE TABLE IF NOT EXISTS imported_match_players (
                    id                SERIAL  PRIMARY KEY,
                    imported_match_id INTEGER NOT NULL
                        REFERENCES imported_matches(id) ON DELETE CASCADE,
                    side              TEXT,
                    player_id         TEXT,
                    name              TEXT,
                    shirt             TEXT,
                    starter           BOOLEAN,
                    rating            TEXT,
                    x_norm            NUMERIC,
                    y_norm            NUMERIC,
                    goals             INTEGER,
                    assists           INTEGER,
                    yellow            BOOLEAN,
                    red               BOOLEAN,
                    motm              BOOLEAN,
                    subbed_on         TEXT,
                    subbed_off        TEXT
                );

                CREATE TABLE IF NOT EXISTS imported_match_events (
                    id                SERIAL  PRIMARY KEY,
                    imported_match_id INTEGER NOT NULL
                        REFERENCES imported_matches(id) ON DELETE CASCADE,
                    event_type        TEXT,
                    minute            INTEGER,
                    player            TEXT,
                    team              TEXT,
                    detail            TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_imported_matches_source
                    ON imported_matches(source, source_match_id);
                CREATE INDEX IF NOT EXISTS idx_imported_matches_date
                    ON imported_matches(match_date);
                CREATE INDEX IF NOT EXISTS idx_imp_match_players_match
                    ON imported_match_players(imported_match_id);
                CREATE INDEX IF NOT EXISTS idx_imp_match_events_match
                    ON imported_match_events(imported_match_id);
            """)


def upsert_player(player: dict):
    """Insert or replace a player and all their related rows."""
    pid = player["id"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO players
                    (id, slug, name, image_url, position, nationality,
                     age, club, jersey_number, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    slug          = EXCLUDED.slug,
                    name          = EXCLUDED.name,
                    image_url     = EXCLUDED.image_url,
                    position      = EXCLUDED.position,
                    nationality   = EXCLUDED.nationality,
                    age           = EXCLUDED.age,
                    club          = EXCLUDED.club,
                    jersey_number = EXCLUDED.jersey_number,
                    fetched_at    = NOW()
            """, (
                pid,
                player.get("slug", ""),
                player.get("name"),
                player.get("image_url"),
                player.get("position"),
                player.get("nationality"),
                player.get("age"),
                player.get("club"),
                player.get("jersey_number"),
            ))

            cur.execute("DELETE FROM season_stats WHERE player_id = %s", (pid,))
            psycopg2.extras.execute_values(cur,
                "INSERT INTO season_stats (player_id, label, value) VALUES %s",
                [(pid, k, str(v)) for k, v in (player.get("season_stats") or {}).items()],
            )

            cur.execute("DELETE FROM career WHERE player_id = %s", (pid,))
            psycopg2.extras.execute_values(cur, """
                INSERT INTO career
                    (player_id, team, start_date, end_date, appearances, goals, assists)
                VALUES %s
            """, [
                (pid, c.get("team"), c.get("start"), c.get("end"),
                 c.get("appearances"), c.get("goals"), c.get("assists"))
                for c in (player.get("career") or [])
            ])

            cur.execute("DELETE FROM matches WHERE player_id = %s", (pid,))
            psycopg2.extras.execute_values(cur, """
                INSERT INTO matches
                    (player_id, match_date, fixture, league, score, result,
                     mins, goals, assists, rating, motm, url)
                VALUES %s
            """, [
                (pid, m.get("date"), m.get("fixture"), m.get("league"),
                 m.get("score"), m.get("result"), m.get("mins"),
                 m.get("goals"), m.get("assists"), m.get("rating"),
                 bool(m.get("motm")), m.get("url"))
                for m in (player.get("matches") or [])
            ])


def load_player(player_id: int) -> Optional[dict]:
    """Return a player dict from the DB, or None if not found."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM players WHERE id = %s", (player_id,))
            row = cur.fetchone()
            if not row:
                return None

            cur.execute(
                "SELECT label, value FROM season_stats WHERE player_id = %s", (player_id,)
            )
            stats = cur.fetchall()

            cur.execute(
                "SELECT team, start_date, end_date, appearances, goals, assists "
                "FROM career WHERE player_id = %s", (player_id,)
            )
            career = cur.fetchall()

            cur.execute(
                "SELECT match_date, fixture, league, score, result, mins, "
                "goals, assists, rating, motm, url "
                "FROM matches WHERE player_id = %s ORDER BY match_date DESC",
                (player_id,)
            )
            matches = cur.fetchall()

    return {
        "id":            row["id"],
        "slug":          row["slug"],
        "name":          row["name"],
        "image_url":     row["image_url"],
        "position":      row["position"],
        "nationality":   row["nationality"],
        "age":           row["age"],
        "club":          row["club"],
        "jersey_number": row["jersey_number"],
        "fetched_at":    str(row["fetched_at"]),
        "season_stats":  {r["label"]: r["value"] for r in stats},
        "career":        [
            {"team": r["team"], "start": r["start_date"], "end": r["end_date"],
             "appearances": r["appearances"], "goals": r["goals"], "assists": r["assists"]}
            for r in career
        ],
        "matches":       [
            {"date": r["match_date"], "fixture": r["fixture"], "league": r["league"],
             "score": r["score"], "result": r["result"], "mins": r["mins"],
             "goals": r["goals"], "assists": r["assists"], "rating": r["rating"],
             "motm": bool(r["motm"]), "url": r["url"]}
            for r in matches
        ],
    }


def list_players() -> list[dict]:
    """Return all stored players ordered by name."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, slug, name, club, position, nationality, fetched_at "
                "FROM players ORDER BY name"
            )
            return [dict(r) for r in cur.fetchall()]


# ── Imported match helpers ────────────────────────────────────────────────────

def upsert_imported_match(match: dict, source: str, match_url: str):
    """Insert or update an imported match with its players and events."""
    source_match_id = str(match.get("match_id") or "")
    if not source_match_id:
        raise ValueError("match dict has no match_id — cannot persist without a stable key")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO imported_matches
                    (source, source_match_id, match_url, match_date, league,
                     home_team, away_team, home_id, away_id, score,
                     home_formation, away_formation, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                ON CONFLICT (source, source_match_id) DO UPDATE SET
                    match_url      = EXCLUDED.match_url,
                    match_date     = EXCLUDED.match_date,
                    league         = EXCLUDED.league,
                    home_team      = EXCLUDED.home_team,
                    away_team      = EXCLUDED.away_team,
                    home_id        = EXCLUDED.home_id,
                    away_id        = EXCLUDED.away_id,
                    score          = EXCLUDED.score,
                    home_formation = EXCLUDED.home_formation,
                    away_formation = EXCLUDED.away_formation,
                    fetched_at     = NOW()
                RETURNING id
            """, (
                source, source_match_id, match_url,
                match.get("date"), match.get("league"),
                match.get("home_team"), match.get("away_team"),
                str(match.get("home_id") or ""), str(match.get("away_id") or ""),
                match.get("score"),
                match.get("home_formation"), match.get("away_formation"),
            ))
            imported_id = cur.fetchone()[0]

            cur.execute(
                "DELETE FROM imported_match_players WHERE imported_match_id = %s",
                (imported_id,),
            )
            players = []
            for side, lineup_key in (("home", "home_lineup"), ("away", "away_lineup")):
                for p in match.get(lineup_key) or []:
                    players.append((
                        imported_id, side,
                        str(p.get("id") or ""), p.get("name"), str(p.get("shirt") or ""),
                        bool(p.get("starter")), str(p.get("rating") or ""),
                        p.get("x_norm"), p.get("y_norm"),
                        int(p.get("goals") or 0), int(p.get("assists") or 0),
                        bool(p.get("yellow")), bool(p.get("red")), bool(p.get("motm")),
                        p.get("subbed_on"), p.get("subbed_off"),
                    ))
            if players:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO imported_match_players
                        (imported_match_id, side, player_id, name, shirt,
                         starter, rating, x_norm, y_norm,
                         goals, assists, yellow, red, motm,
                         subbed_on, subbed_off)
                    VALUES %s
                """, players)

            cur.execute(
                "DELETE FROM imported_match_events WHERE imported_match_id = %s",
                (imported_id,),
            )
            events = [
                (imported_id, e.get("type"), e.get("minute"),
                 e.get("player"), e.get("team"), e.get("detail"))
                for e in (match.get("events") or [])
            ]
            if events:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO imported_match_events
                        (imported_match_id, event_type, minute, player, team, detail)
                    VALUES %s
                """, events)


def load_imported_match(source: str, source_match_id: str) -> Optional[dict]:
    """Return a full imported match dict (lineups + events), or None."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM imported_matches "
                "WHERE source = %s AND source_match_id = %s",
                (source, source_match_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            im_id = row["id"]
            cur.execute(
                "SELECT * FROM imported_match_players "
                "WHERE imported_match_id = %s "
                "ORDER BY side, starter DESC, shirt",
                (im_id,),
            )
            players = cur.fetchall()

            cur.execute(
                "SELECT * FROM imported_match_events "
                "WHERE imported_match_id = %s ORDER BY minute NULLS LAST",
                (im_id,),
            )
            raw_events = cur.fetchall()

    match = dict(row)
    match["home_lineup"] = [dict(p) for p in players if p["side"] == "home"]
    match["away_lineup"] = [dict(p) for p in players if p["side"] == "away"]
    match["events"]      = [dict(e) for e in raw_events]
    return match


def list_imported_matches(limit: int = 100) -> list[dict]:
    """Return imported matches newest-first, up to limit rows."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, source, source_match_id, match_url,
                       match_date, league, home_team, away_team, score,
                       home_formation, away_formation, fetched_at
                FROM imported_matches
                ORDER BY match_date DESC NULLS LAST, fetched_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


