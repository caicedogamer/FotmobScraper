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


def _dsn() -> str:
    return (
        f"host={os.getenv('PG_HOST', 'localhost')} "
        f"port={os.getenv('PG_PORT', '5432')} "
        f"dbname={os.getenv('PG_DB', 'fotmob')} "
        f"user={os.getenv('PG_USER', 'postgres')} "
        f"password={os.getenv('PG_PASSWORD', 'postgres')}"
    )


@contextmanager
def get_conn():
    conn = psycopg2.connect(_dsn())
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


# Create tables on import
init_db()
