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
