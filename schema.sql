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
