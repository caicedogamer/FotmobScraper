# FotMob Scraper

A Python data pipeline that reverse-engineers FotMob's Next.js JSON API to extract player statistics, persists them in PostgreSQL, exposes them through a dark-themed Flask web interface, surfaces them via Discord slash commands, and predicts upcoming match scores using a Poisson regression model seeded from live team form data.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Reverse-Engineering FotMob's API](#reverse-engineering-fotmobs-api)
3. [Data Pipeline](#data-pipeline)
4. [Match Score Predictor](#match-score-predictor)
5. [Database Schema](#database-schema)
6. [Flask Web Application](#flask-web-application)
7. [Discord Bot](#discord-bot)
8. [Bulk Scraper](#bulk-scraper)
9. [Lineup Renderer](#lineup-renderer)
10. [Setup & Configuration](#setup--configuration)
11. [Running](#running)
12. [Dependencies](#dependencies)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         FotMob (external)                       │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐ │
│  │  Homepage    │   │  Player/Match    │   │  apigw search   │ │
│  │  (cookie     │   │  HTML page       │   │  + team/league  │ │
│  │   warmup)    │   │  (buildId)       │   │  REST API       │ │
│  └──────┬───────┘   └────────┬─────────┘   └────────┬────────┘ │
└─────────┼───────────────────┼──────────────────────┼───────────┘
          │                   │                       │
          ▼                   ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                         scraper.py                              │
│  make_session() → get_build_id() → fetch_player_json()          │
│  fetch_match_json() → parse_player() / parse_match()            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┼────────────────┐
              ▼             ▼                ▼
        ┌──────────┐  ┌──────────┐   ┌──────────────┐
        │   db.py  │  │  app.py  │   │  predictor.py│
        │ PostgreSQL│  │  Flask   │   │  Poisson     │
        │  upsert  │  │  :5000   │   │  model       │
        └──────────┘  └──────────┘   └──────────────┘
                            │
                      ┌─────┴──────┐
                      │   bot.py   │
                      │  Discord   │
                      │  slash cmds│
                      └────────────┘
```

**Module responsibilities:**

| Module | Responsibility |
|---|---|
| `scraper.py` | HTTP session management, buildId extraction, JSON fetching, response parsing |
| `db.py` | PostgreSQL connection, schema init, upsert/load/list operations |
| `app.py` | Flask routes, HTML templates, SSE streaming, search proxy |
| `bot.py` | Discord gateway, slash command registration, embed construction |
| `bulk.py` | Concurrent multi-player scraping via `ThreadPoolExecutor` |
| `predictor.py` | FotMob league/team API, Poisson PMF model, 1-hour in-memory cache |
| `pitch.py` | Pillow-based pitch diagram renderer for match lineups |

---

## Reverse-Engineering FotMob's API

### The buildId problem

FotMob is a Next.js application. Next.js serialises page data as `/_next/data/{buildId}/en/{path}.json`, where `buildId` is a hash regenerated on every deployment (typically multiple times per day). There is no stable endpoint — every JSON URL expires when the build rotates.

**Solution:** before fetching any JSON, the scraper fetches the corresponding HTML page and extracts the `buildId` with a regex:

```python
match = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
build_id = match.group(1)
```

The `buildId` is embedded in the `__NEXT_DATA__` script tag that Next.js injects into every page. Extracting it from the player HTML page guarantees it matches the currently deployed build.

### Session warmup & cookie handling

FotMob's CDN enforces bot detection at the cookie layer. A cold `requests.Session` without prior cookies will receive empty or blocked responses. The session must be warmed against the homepage first:

```python
def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(SESSION_HEADERS)
    session.get(BASE_URL, timeout=15)   # picks up __cf_bm, _fotmob_* cookies
    return session
```

The session object carries cookies across all subsequent requests automatically.

### The Brotli trap

A critical non-obvious constraint: **`Accept-Encoding: gzip, deflate, br` must not be sent.**

When the browser advertises Brotli support (`br`), FotMob's CDN responds with `Content-Encoding: br`. The `requests` library cannot decode Brotli natively — the response body arrives as raw binary garbage with no decoding error raised. The failure is silent.

The fix is to omit `Accept-Encoding` entirely from `SESSION_HEADERS`. `requests` will then negotiate `gzip` only, which it decodes transparently.

### Player JSON endpoint construction

```
https://www.fotmob.com/_next/data/{buildId}/en/players/{playerId}/{slug}.json
  ?lng=en&id={playerId}&slug={slug}
```

### Match JSON endpoint construction

FotMob has used two URL formats historically:

| Format | Path pattern | JSON path |
|---|---|---|
| New (current) | `/matches/{fixture-slug}/{code}#{matchId}` | `/_next/data/{buildId}/en/matches/{fixture-slug}/{code}.json?matchId={id}` |
| Old | `/match/{matchId}/{tab}/{slug}` | `/_next/data/{buildId}/en/match/{matchId}/{tab}/{slug}.json?matchId={id}` |

`fetch_match_json()` detects the format from the first path segment and constructs the correct URL for both variants.

### Search API

Player search hits a stable REST endpoint that does **not** require a buildId:

```
GET https://apigw.fotmob.com/searchapi/suggest?term={query}&lang=en
```

Response structure:
```json
{
  "squadMemberSuggest": [
    {
      "options": [
        {
          "text": "Erling Haaland|...",
          "payload": { "id": 961995, "teamName": "Man City", "isCoach": false }
        }
      ]
    }
  ]
}
```

Coaches are filtered out via `payload.isCoach`. The player slug is derived deterministically from the name: lowercase, strip non-word characters, collapse spaces to hyphens — matching FotMob's own slug generation.

---

## Data Pipeline

### Player data shape

`parse_player()` normalises the raw `pageProps.data` blob into a flat dict:

```python
{
  "id":            961995,
  "slug":          "erling-haaland",
  "name":          "Erling Haaland",
  "image_url":     "https://images.fotmob.com/image_resources/playerimages/961995.png",
  "position":      "Centre-Forward",
  "nationality":   "Norway",
  "age":           23,
  "club":          "Man City",
  "jersey_number": 9,
  "season_stats":  {"Goals": 27, "Assists": 5, ...},   # current league only
  "career":        [{"team": "...", "start": "...", ...}],
  "matches":       [{"date": "...", "fixture": "...", "result": "W", ...}],
}
```

**Season stats** are extracted from `pageProps.data.mainLeague.stats` — a `[{title, value}]` list scoped to the player's primary competition.

**Career** comes from `pageProps.data.careerHistory.careerItems.senior.teamEntries`. Appearances, goals, and assists are stored as `TEXT` in PostgreSQL because FotMob returns them as strings (sometimes with trailing asterisks for estimated figures).

**Recent matches** skip `onBench: true` rows. The fixture string is constructed with the home team first (`Team A vs Team B`) regardless of which side the player was on, matching FotMob's canonical display format.

### Match data shape

`parse_match()` extracts from three sections of the match JSON:

| Section | Used for |
|---|---|
| `pageProps.general` | Teams, IDs, date, league name |
| `pageProps.header` | Score string, goal events, red card events |
| `pageProps.content.lineup` | Starter/sub lists, formation, x/y coordinates |
| `pageProps.content.matchFacts.events` | Yellow card player IDs |

Player positions on the pitch are encoded as normalised `verticalLayout.{x, y}` floats (0–1 range), used directly by `pitch.py` to place player tokens.

---

## Match Score Predictor

### Model

`predictor.py` implements a **bivariate independent Poisson model** — the canonical approach for football score prediction (Dixon & Coles, 1997).

#### 1. Team form extraction

For each team in an upcoming fixture, the FotMob team API is queried:

```
GET https://www.fotmob.com/api/teams?id={teamId}
```

The response's `recentResults.matches` array is iterated. For each match, the team's goals-for and goals-against are determined by checking whether `match.home.id == teamId`:

```python
if str(home_id) == str(team_id):
    raw.append((home_score, away_score))   # team was home
else:
    raw.append((away_score, home_score))   # team was away
```

Averages are computed over the last 8 matches:

```
gf̄ = Σ goals_for  / n
gā = Σ goals_against / n
```

If `recentResults` is absent, a fallback path checks `fixtures.previousMatches`. If both paths yield no data, a league-average default (`gf=1.3, ga=1.1`) is used.

Results are cached in a module-level dict with a 1-hour TTL to avoid redundant API calls across requests.

#### 2. Expected goals (λ)

```
λ_home = ((home.gf̄ + away.gā) / 2) × HOME_ADVANTAGE
λ_away =  (away.gf̄ + home.gā) / 2
```

`HOME_ADVANTAGE = 1.15` — a 15% uplift reflecting the empirical advantage of playing at home (crowd effect, travel fatigue, referee bias). This constant is consistent with observed home-team goal ratios across European top divisions (~1.35 home goals vs ~1.10 away goals per game historically).

The averaging of attack and defence strengths acts as a simplified Dixon-Coles attack/defence factorisation without requiring a full league-wide parameter fit.

#### 3. Poisson PMF enumeration

The joint probability of a specific scoreline `(h, a)` under independent Poisson variables:

```
P(H=h, A=a) = P_pois(h; λ_home) × P_pois(a; λ_away)

              e^(-λ) × λ^k
P_pois(k; λ) = ──────────────
                    k!
```

The model enumerates all `(h, a)` pairs for `h, a ∈ [0, 7]` — covering >99.9% of the probability mass for typical λ values (< 3.5 goals expected):

```python
for h in range(8):
    ph = pmf(h, lam_home)
    for a in range(8):
        p = ph * pmf(a, lam_away)
        if p > best_p:
            best_p, best = p, (h, a)
        # accumulate outcome probabilities
        if h > a: p_home_win += p
        elif h < a: p_away_win += p
        else: p_draw += p
```

#### 4. Output

| Field | Description |
|---|---|
| `scoreline` | Modal scoreline — argmax of the joint PMF |
| `outcome` | Determined from outcome probabilities, not the scoreline |
| `p_home / p_draw / p_away` | Win/draw/win probabilities (sum ≈ 1.0) |
| `confidence` | Probability of the predicted outcome class |
| `xg_home / xg_away` | λ values used as model-implied expected goals |

**Why outcome ≠ scoreline outcome:** The most probable individual scoreline (e.g. 1–1) can differ from the most probable outcome class (e.g. Home Win) because outcome probabilities aggregate over all matching scorelines. A 1–1 draw has a single probability, while "home win" sums probabilities across 1–0, 2–0, 2–1, 3–0, 3–1, 3–2, etc. This distinction is preserved explicitly in the output.

### Supported leagues

| Key | League | FotMob ID |
|---|---|---|
| `premier_league` | Premier League | 47 |
| `la_liga` | La Liga | 87 |
| `serie_a` | Serie A | 55 |
| `bundesliga` | Bundesliga | 54 |
| `ligue_1` | Ligue 1 | 53 |
| `liga_betplay` | Liga BetPlay Dimayor | 241 |

---

## Database Schema

PostgreSQL. All child tables `ON DELETE CASCADE` from `players`.

```sql
CREATE TABLE players (
    id           BIGINT PRIMARY KEY,   -- FotMob player ID
    slug         TEXT NOT NULL,
    name         TEXT,
    image_url    TEXT,
    position     TEXT,
    nationality  TEXT,
    age          INT,
    club         TEXT,
    jersey_number INT,
    fetched_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE season_stats (
    player_id  BIGINT REFERENCES players(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    value      TEXT,                   -- kept as TEXT; cast in SQL if needed
    PRIMARY KEY (player_id, label)
);

CREATE TABLE career (
    id           SERIAL PRIMARY KEY,
    player_id    BIGINT REFERENCES players(id) ON DELETE CASCADE,
    team         TEXT,
    start_date   TEXT,
    end_date     TEXT,
    appearances  TEXT,                 -- TEXT — FotMob returns "123*" for estimates
    goals        TEXT,
    assists      TEXT
);

CREATE TABLE matches (
    id          SERIAL PRIMARY KEY,
    player_id   BIGINT REFERENCES players(id) ON DELETE CASCADE,
    match_date  DATE,
    fixture     TEXT,
    league      TEXT,
    score       TEXT,
    result      CHAR(1),               -- W / D / L
    mins        INT,
    goals       INT,
    assists     INT,
    rating      NUMERIC(4,2),
    motm        BOOLEAN DEFAULT FALSE,
    url         TEXT
);
```

**Upsert strategy:** `upsert_player()` performs a full delete+re-insert of all child rows on every refresh. This is intentionally simple — child rows have no stable natural key from FotMob's API (match IDs are not returned in player JSON), so partial updates would require fuzzy matching. Atomic delete+insert within a transaction is safer and fast enough for the data volumes involved.

**Indexes** on `(player_id, match_date)` for `matches` and `player_id` for `season_stats`/`career` support the typical access pattern (load all data for one player).

---

## Flask Web Application

### Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Player dashboard. Accepts `?player_id=&slug=` and `?refresh=1` |
| `GET` | `/search` | Search proxy → FotMob suggest API. Returns `[{id, name, slug, team}]` |
| `GET` | `/bulk` | Bulk import form |
| `POST` | `/bulk/stream` | SSE endpoint — streams `data: {...}\n\n` as each player completes |
| `GET` | `/predictions` | Match predictor. Accepts `?league={key}` |

### Cache flow for `GET /`

```
player_id + slug present?
  └─ yes → DB lookup (load_player)
      └─ hit + no ?refresh → serve from DB (from_cache=True)
      └─ miss or ?refresh → scrape FotMob → upsert_player → serve live
```

### SSE streaming (`/bulk/stream`)

The bulk import endpoint uses a producer/consumer pattern to stream results without blocking:

```
POST /bulk/stream
    │
    ├── spawns daemon Thread → bulk_scrape(..., progress_cb=on_result)
    │       each finished player → on_result(r) → result_queue.put(...)
    │
    └── generator reads result_queue
            → yields "data: {json}\n\n" per result
            → terminates on sentinel None
```

Flask's `Response(stream_with_context(generator()), mimetype="text/event-stream")` keeps the connection open. The `X-Accel-Buffering: no` header disables nginx proxy buffering if deployed behind a reverse proxy.

### Search autocomplete

Client-side debounce (300 ms, minimum 2 characters) fires a `fetch('/search?q=...')` request. The server proxies to FotMob's suggest API and returns a filtered JSON array. Selection navigates to `/?player_id=&slug=`.

---

## Discord Bot

Built with `discord.py` using the application commands (slash command) framework.

### Command registration

Commands are registered to `bot.tree` and synced globally on `on_ready`:

```python
await tree.sync()
```

Global sync propagates to all guilds within ~1 hour. For instant testing, sync to a specific guild ID instead.

### Player resolution logic

All player commands share `_resolve_player(name)`:

```
1. list_players() from DB → case-insensitive substring match on name
   → if found: load_player(id) and return (fast path, no network)

2. search_players(name) via FotMob suggest API
   → top result: make_session() → fetch_player_json() → parse_player()
   (live scrape, ~3–5 s)
```

All blocking I/O runs in a thread pool via `loop.run_in_executor(None, fn)` to keep the event loop unblocked.

### Slash commands

| Command | Args | Description |
|---|---|---|
| `/player` | `name` | Full profile embed with bio, form strip, last 5 matches, G+A |
| `/stats` | `name` | Season stats with xG/xA, rating, visual progress bars for percentages |
| `/matches` | `name`, `[count=8]` | Match log with scores, G, A, rating, MOTM indicator |
| `/match` | `name`, `[number=1]` | Lineup pitch image + key events for a specific recent match |
| `/career` | `name` | Club-by-club history with career totals |
| `/compare` | `player1`, `player2` | Side-by-side with 🥇/🥈 medal per category |
| `/predict` | `league` | Upcoming match predictions (Poisson model) with win/draw/win % |
| `/fotmob_help` | — | Command reference |

### Embed construction patterns

**Form strip:** `"".join(RESULT_EMOJI.get(m["result"], "⚪") for m in matches[:n])` — `🟢🟢🔴🟡🟢`

**Dominant colour:** embed colour is set to green/yellow/red based on majority result in the last N matches.

**Stats progress bars:** percentage-like stat values (detected by `%` in label or keywords like `accuracy`, `success`) render as `█████░░░░░░` ASCII bars (10 chars, filled proportionally to value/10).

**Position colour mapping:** embed accent colour reflects playing position — red for forwards, blue for midfielders, green for defenders, orange for goalkeepers.

---

## Bulk Scraper

`bulk.py` implements concurrent scraping with backpressure controls.

### Thread pool design

```python
bulk_scrape(names, workers=3, delay=1.0, progress_cb=None)
```

```
ThreadPoolExecutor(max_workers=workers)
    │
    ├── worker 0: sleep(0 × delay)   → scrape name[0]
    ├── worker 1: sleep(1 × delay)   → scrape name[1]
    └── worker 2: sleep(2 × delay)   → scrape name[2]
         ... (round-robin across pool)
```

**Staggered startup:** worker `i` sleeps `i × delay` seconds before its first request. This prevents a request burst at `t=0` that would trigger FotMob's rate limiter. After the initial stagger, workers run continuously with no inter-request sleep — the delay only applies to the first request per worker.

**Worker function `_scrape_one`:**
```
search_players(name) → top result
    → make_session()                   # fresh session per worker
    → fetch_player_json(session, ...)  # buildId + data fetch
    → parse_player(raw)
    → upsert_player(player)
    → ScrapeResult(status="ok", ...)
```

Each worker gets its own `requests.Session` — sharing sessions across threads is not safe due to cookie jar mutation.

**Result dataclass:**
```python
@dataclass
class ScrapeResult:
    name:    str              # original search query
    status:  str              # "ok" | "not_found" | "error"
    player:  dict | None
    matches: int              # number of match rows upserted
    error:   str | None
```

`progress_cb(ScrapeResult)` is called from worker threads — if used from Flask SSE, the callback must be thread-safe (a `queue.Queue` is the safe pattern, as used in `app.py`).

**Recommended limits:** `workers ≤ 3`, `delay ≥ 1.0`. Beyond 3 concurrent sessions FotMob's edge infrastructure begins rate-limiting with 429 or empty responses.

---

## Lineup Renderer

`pitch.py` uses **Pillow** to generate a match lineup image from parsed match data.

### Coordinate system

FotMob encodes player positions as normalised `verticalLayout.{x, y}` floats in `[0, 1]`:
- `x=0.5` is the centre of the pitch width
- `y=0` is the defensive end, `y=1` is the attacking end

The renderer maps these to pixel coordinates on a 2D pitch diagram, draws a circle for each player (coloured by team), overlays shirt number and name, and highlights the searched player with a gold ring. MOTM players receive a star indicator.

---

## Setup & Configuration

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- A Discord application with bot token (for `bot.py`)

### Environment variables (`.env`)

```env
PG_HOST=localhost
PG_PORT=5432
PG_DB=fotmob
PG_USER=postgres
PG_PASSWORD=yourpassword
DISCORD_TOKEN=your_bot_token_here
```

`.env` is loaded via `python-dotenv` on import of `db.py` and `bot.py`. The file is gitignored — never commit credentials.

### Database initialisation

```bash
# 1. Create the database
psql -U postgres -c "CREATE DATABASE fotmob;"

# 2. Run the schema
psql -U postgres -d fotmob -f schema.sql
```

`db.py` also calls `init_db()` on import, which issues `CREATE TABLE IF NOT EXISTS` statements — so the schema is auto-created on first run if the DB already exists.

---

## Running

```bash
# Web app (Flask dev server, port 5000)
python app.py

# Discord bot
python bot.py

# CLI scrape (single player)
python scraper.py 961995 erling-haaland
python scraper.py 961995 erling-haaland --raw   # dump raw JSON

# Bulk scrape from CLI
python bulk.py "erling haaland" "kylian mbappe" "vinicius junior"
python bulk.py --file players.txt --workers 2 --delay 1.5
```

`players.txt` format: one name per line, lines beginning with `#` are ignored.

Flask must run with `threaded=True` (default in `app.run()`) for SSE streaming to work — each SSE connection holds a thread for its lifetime.

---

## Dependencies

```
requests          HTTP client — session management, cookie jar, gzip decoding
flask             Web framework — routing, template rendering, SSE streaming
psycopg2-binary   PostgreSQL adapter
python-dotenv     .env file loader
discord.py        Discord gateway + application commands framework
Pillow            Image generation for pitch diagrams (pitch.py)
```

Install:
```bash
pip install requests flask psycopg2-binary python-dotenv discord.py Pillow
```
