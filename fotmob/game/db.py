"""Database setup and seed helpers for the Discord card minigame."""

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.cards import metadata_card_dicts, seed_card_dicts
from fotmob.game.odds import PACK_DEFINITIONS


def init_game_db():
    """Create game tables and seed default packs/cards."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS game_users (
                    discord_id    TEXT PRIMARY KEY,
                    coins         INTEGER NOT NULL DEFAULT 2500,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_daily_at TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS game_player_cards (
                    id               SERIAL PRIMARY KEY,
                    player_source_id INTEGER,
                    name             TEXT NOT NULL,
                    club             TEXT,
                    nationality      TEXT,
                    position         TEXT,
                    rating           INTEGER NOT NULL,
                    rarity           TEXT NOT NULL,
                    image_url        TEXT,
                    card_type        TEXT NOT NULL DEFAULT 'base',
                    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                    rating_formula_version TEXT,
                    rating_score     NUMERIC,
                    rating_updated_at TIMESTAMPTZ,
                    UNIQUE (name, club, card_type)
                );

                CREATE TABLE IF NOT EXISTS game_inventory (
                    id              SERIAL PRIMARY KEY,
                    discord_id      TEXT NOT NULL REFERENCES game_users(discord_id) ON DELETE CASCADE,
                    card_id         INTEGER NOT NULL REFERENCES game_player_cards(id) ON DELETE CASCADE,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    locked          BOOLEAN NOT NULL DEFAULT FALSE,
                    acquired_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (discord_id, card_id)
                );

                CREATE TABLE IF NOT EXISTS game_pack_types (
                    id                SERIAL PRIMARY KEY,
                    key               TEXT NOT NULL UNIQUE,
                    name              TEXT NOT NULL,
                    price             INTEGER NOT NULL,
                    cards_per_pack    INTEGER NOT NULL,
                    min_rating        INTEGER NOT NULL,
                    guaranteed_rarity TEXT,
                    description       TEXT
                );

                CREATE TABLE IF NOT EXISTS game_pack_openings (
                    id         SERIAL PRIMARY KEY,
                    discord_id TEXT NOT NULL REFERENCES game_users(discord_id) ON DELETE CASCADE,
                    pack_key   TEXT NOT NULL,
                    cost       INTEGER NOT NULL,
                    opened_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS game_pack_opening_items (
                    id             SERIAL PRIMARY KEY,
                    opening_id     INTEGER NOT NULL REFERENCES game_pack_openings(id) ON DELETE CASCADE,
                    card_id        INTEGER NOT NULL REFERENCES game_player_cards(id) ON DELETE CASCADE,
                    is_duplicate   BOOLEAN NOT NULL DEFAULT FALSE,
                    coins_refunded INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_game_inventory_user
                    ON game_inventory(discord_id);
                CREATE INDEX IF NOT EXISTS idx_game_cards_rarity_rating
                    ON game_player_cards(rarity, rating DESC);
                CREATE INDEX IF NOT EXISTS idx_game_openings_user
                    ON game_pack_openings(discord_id, opened_at DESC);
            """)
            cur.execute("""
                ALTER TABLE game_player_cards
                    ADD COLUMN IF NOT EXISTS rating_formula_version TEXT;
                ALTER TABLE game_player_cards
                    ADD COLUMN IF NOT EXISTS rating_score NUMERIC;
                ALTER TABLE game_player_cards
                    ADD COLUMN IF NOT EXISTS rating_updated_at TIMESTAMPTZ;
            """)

            for key, pack in PACK_DEFINITIONS.items():
                cur.execute("""
                    INSERT INTO game_pack_types
                        (key, name, price, cards_per_pack, min_rating,
                         guaranteed_rarity, description)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (key) DO UPDATE SET
                        name = EXCLUDED.name,
                        price = EXCLUDED.price,
                        cards_per_pack = EXCLUDED.cards_per_pack,
                        min_rating = EXCLUDED.min_rating,
                        guaranteed_rarity = EXCLUDED.guaranteed_rarity,
                        description = EXCLUDED.description
                """, (
                    key, pack["name"], pack["price"], pack["cards_per_pack"],
                    pack["min_rating"], pack["guaranteed_rarity"], pack["description"],
                ))

            # Squad tables (added after base game tables so FKs resolve)
            from fotmob.game.squad import init_squad_tables
            init_squad_tables(cur)

            cards = seed_card_dicts(include_metadata=False)
            metadata_cards = metadata_card_dicts()
            cur.execute("SELECT COUNT(*) FROM game_player_cards WHERE card_type = 'metadata'")
            metadata_count = int(cur.fetchone()[0])
            if metadata_count < len(metadata_cards):
                cards.extend(metadata_cards)
            psycopg2.extras.execute_values(cur, """
                INSERT INTO game_player_cards
                    (player_source_id, name, club, nationality, position, rating, rarity, image_url, card_type)
                VALUES %s
                ON CONFLICT (name, club, card_type) DO UPDATE SET
                    player_source_id = EXCLUDED.player_source_id,
                    nationality = EXCLUDED.nationality,
                    position = EXCLUDED.position,
                    rating = EXCLUDED.rating,
                    rarity = EXCLUDED.rarity,
                    image_url = EXCLUDED.image_url,
                    is_active = TRUE
            """, [
                (
                    c.get("player_source_id"),
                    c["name"], c["club"], c["nationality"], c["position"],
                    c["rating"], c["rarity"], c["image_url"], c["card_type"],
                )
                for c in cards
            ])


def ensure_user(cur, discord_id: str):
    cur.execute("""
        INSERT INTO game_users (discord_id, coins)
        VALUES (%s, 2500)
        ON CONFLICT (discord_id) DO NOTHING
    """, (discord_id,))


def fetch_user(cur, discord_id: str) -> dict:
    cur.execute("SELECT * FROM game_users WHERE discord_id = %s", (discord_id,))
    return dict(cur.fetchone())
