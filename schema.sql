-- ============================================================
-- STEP 1 — run this while connected to the "postgres" database
-- ============================================================
CREATE DATABASE fotmob;


-- ============================================================
-- STEP 2 — open a new connection to "fotmob", then run below
-- ============================================================

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER      PRIMARY KEY,
    slug          TEXT         NOT NULL,
    name          TEXT,
    image_url     TEXT,
    position      TEXT,
    nationality   TEXT,
    age           INTEGER,
    club          TEXT,
    jersey_number INTEGER,
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS season_stats (
    id        SERIAL   PRIMARY KEY,
    player_id INTEGER  NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    label     TEXT     NOT NULL,
    value     TEXT
);

CREATE INDEX IF NOT EXISTS idx_season_stats_player ON season_stats(player_id);

CREATE TABLE IF NOT EXISTS career (
    id          SERIAL   PRIMARY KEY,
    player_id   INTEGER  NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team        TEXT,
    start_date  TEXT,
    end_date    TEXT,
    appearances TEXT,
    goals       TEXT,
    assists     TEXT
);

CREATE INDEX IF NOT EXISTS idx_career_player ON career(player_id);

CREATE TABLE IF NOT EXISTS matches (
    id          SERIAL   PRIMARY KEY,
    player_id   INTEGER  NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    match_date  TEXT,
    fixture     TEXT,
    league      TEXT,
    score       TEXT,
    result      TEXT,
    mins        INTEGER,
    goals       INTEGER,
    assists     INTEGER,
    rating      TEXT,
    motm        BOOLEAN  DEFAULT FALSE,
    url         TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_player      ON matches(player_id);
CREATE INDEX IF NOT EXISTS idx_matches_player_date ON matches(player_id, match_date DESC);

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
    imported_match_id INTEGER NOT NULL REFERENCES imported_matches(id) ON DELETE CASCADE,
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
    imported_match_id INTEGER NOT NULL REFERENCES imported_matches(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS game_squads (
    discord_id  TEXT PRIMARY KEY REFERENCES game_users(discord_id) ON DELETE CASCADE,
    formation   TEXT NOT NULL DEFAULT '4-3-3',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS game_squad_slots (
    id           SERIAL PRIMARY KEY,
    discord_id   TEXT NOT NULL REFERENCES game_squads(discord_id) ON DELETE CASCADE,
    slot_key     TEXT NOT NULL,
    inventory_id INTEGER REFERENCES game_inventory(id) ON DELETE SET NULL,
    UNIQUE (discord_id, slot_key)
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
CREATE INDEX IF NOT EXISTS idx_squad_slots_user
    ON game_squad_slots(discord_id);
CREATE INDEX IF NOT EXISTS idx_game_cards_rarity_rating
    ON game_player_cards(rarity, rating DESC);
CREATE INDEX IF NOT EXISTS idx_game_openings_user
    ON game_pack_openings(discord_id, opened_at DESC);
