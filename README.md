# FotMob Scraper

Scrapes player stats from FotMob, stores them in PostgreSQL, and serves them through a Flask UI and Discord bot. Also predicts upcoming match outcomes with an optional trained ML model and a Poisson fallback.

---

## What's in the box

```
app.py           — Flask web app (port 5000)
bot.py           — Discord slash commands
bulk.py          — concurrent multi-player scraper
bulk_matches.py  — concurrent multi-match importer (CLI + library)
train_model.py   — offline model training CLI
scraper.py       — compatibility wrapper for the player scraper CLI
schema.sql       — DDL if you want to run it manually

fotmob/
  scraper.py       — HTTP scraping, buildId extraction, response parsing
  db.py            — PostgreSQL layer
  predictor.py     — match predictor facade (ML when trained, Poisson fallback)
  ml_predictor.py  — historical data collection, features, ML training/runtime
  fetch_backend.py — optional requests/Scrapling fetch backend
  pitch.py         — lineup image renderer (Pillow)
  providers/
    __init__.py    — provider registry and dispatch
    fotmob.py      — FotMob match provider
    sofascore.py   — Sofascore stub (NotImplementedError, see file for guide)
```

---

## Setup

**Requirements:** Python 3.11+, PostgreSQL 14+, a Discord bot token if you want the bot.

```bash
pip install requests flask psycopg2-binary python-dotenv discord.py Pillow
```

**Optional — Scrapling fetch backend** (improves success rate against anti-bot pages):

```bash
pip install "scrapling[fetchers]"
scrapling install    # downloads browser fingerprinting assets (needed by some Scrapling modes)
```

The app works without Scrapling. It is only invoked when `--engine scrapling` or `--engine auto` is passed, or when the engine selector in the web UI is changed from the default. See [Fetch engine](#fetch-engine) below.

**Optional — ML prediction backend**:

```bash
pip install xgboost scikit-learn pandas numpy joblib
```

XGBoost is preferred when installed. If it is missing, training falls back to scikit-learn models. If no ML model artifact exists, predictions use the Poisson baseline automatically.

Create `.env`:
```env
PG_HOST=localhost
PG_PORT=5432
PG_DB=fotmob
PG_USER=postgres
PG_PASSWORD=yourpassword
DISCORD_TOKEN=your_bot_token_here
```

Create the database:
```bash
psql -U postgres -c "CREATE DATABASE fotmob;"
psql -U postgres -d fotmob -f schema.sql
```

`app.py`, `bot.py`, and `bulk*.py` each call `init_db()` at startup, which runs `CREATE TABLE IF NOT EXISTS` for all tables and indexes — so the schema self-initialises without needing to run `schema.sql` manually.

---

## Running

```bash
python app.py       # Flask on :5000
python bot.py       # Discord bot

# scrape one player from the CLI
python scraper.py 961995 erling-haaland
python scraper.py 961995 erling-haaland --raw         # dump raw JSON
python scraper.py 961995 erling-haaland --engine auto # Scrapling fallback on block

# bulk player scrape
python bulk.py "erling haaland" "kylian mbappe"
python bulk.py --file players.txt --workers 2 --delay 1.5
python bulk.py --file players.txt --engine auto        # auto-fallback to Scrapling

# bulk match import
python bulk_matches.py https://www.fotmob.com/matches/man-city-vs-arsenal/...
python bulk_matches.py --file matches.txt
python bulk_matches.py --provider fotmob --workers 2 --delay 1.0 --file matches.txt
python bulk_matches.py --file matches.txt --engine auto
```

`players.txt`: one name per line, `#` lines are ignored.

`matches.txt`: one FotMob match URL per line, `#` lines and blank lines are ignored:
```
# Premier League 2024-25
https://www.fotmob.com/matches/man-city-vs-arsenal/AbCdEf#4813688
https://www.fotmob.com/matches/chelsea-vs-liverpool/GhIjKl#4813700

# La Liga
https://www.fotmob.com/matches/real-madrid-vs-barcelona/MnOpQr#4900000
```

The web UI at `/matches/bulk` provides the same functionality in a browser with live SSE progress streaming. Imported matches are listed at `/matches/imported`.

---

## How the scraping works

FotMob is Next.js. Their data lives at `/_next/data/{buildId}/en/players/{id}/{slug}.json`, but `buildId` rotates on every deployment — sometimes multiple times a day. There's no stable URL.

The fix: fetch the player's HTML page first, pull the `buildId` out of it with a regex, then build the JSON URL. That way the ID is always current.

```python
match = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
```

**Cookie warmup:** hitting FotMob cold gets you blocked or empty responses. The session always GETs the homepage first to pick up the Cloudflare and FotMob cookies before doing anything else.

**The Brotli problem:** if you send `Accept-Encoding: gzip, deflate, br`, FotMob responds with Brotli. `requests` can't decode Brotli and silently returns garbage bytes — no exception, no warning, just broken JSON. The fix is to omit `Accept-Encoding` entirely and let `requests` negotiate gzip on its own. This one burned some time.

**Search** hits `apigw.fotmob.com/searchapi/suggest` which doesn't need a buildId and is stable.

---

## Fetch engine

`fotmob/fetch_backend.py` provides a thin abstraction over HTTP backends so the scraper can recover from anti-bot blocks without a full rewrite.

| Engine | Behaviour |
|---|---|
| `requests` | Default. Uses `requests.Session` with cookie warmup. |
| `scrapling` | Uses Scrapling's `Fetcher(auto_match=False)` (HTTP, no browser). Requires `pip install "scrapling[fetchers]"`. |
| `auto` | Tries `requests` first. If the response is a 403/429/503 or the HTML looks like a Cloudflare challenge page, logs the fallback and retries with Scrapling. |

**What uses the engine and what doesn't:**

- HTML page fetches (for `buildId` extraction) respect the engine — these are the calls that get blocked.
- `_next/data` JSON endpoint calls always use the `requests` session because they depend on warmup cookies that Scrapling (stateless) cannot carry.

**When Scrapling is not installed:**

- `engine="requests"` works normally — no import errors.
- `engine="scrapling"` raises `ImportError` with install instructions.
- `engine="auto"` behaves as `requests`-only; if `requests` is blocked it raises `ImportError` rather than silently degrading.

---

## Bulk scraper

```python
bulk_scrape(names, workers=3, delay=1.0, progress_cb=None)
```

Workers are staggered: worker `i` sleeps `i * delay` seconds before its first request. This avoids the burst at `t=0` that gets you rate-limited. Each worker gets its own session (sharing sessions across threads isn't safe due to the cookie jar).

Keep `workers ≤ 3`. Beyond that FotMob starts returning 429s or empty responses.

The web UI at `/bulk` streams results back as SSE — the Flask route puts a thread on `bulk_scrape` and the generator drains a `queue.Queue` as players finish.

---

## Match predictor

`predictor.py` supports three modes:

| mode | behaviour |
|---|---|
| `auto` | Use the trained ML model if `data/match_model.joblib` exists; otherwise Poisson |
| `ml` | Try the trained ML model, then fall back to Poisson if unavailable |
| `poisson` | Force the built-in Poisson baseline |

Discord: `/predict <league> [model]`  
Web: `/predictions?league=premier_league&model=auto`

### Training the ML model

Training is offline. The Discord bot only loads the saved artifact.

```bash
# one league, all accessible seasons
python train_model.py --league premier_league

# all supported leagues
python train_model.py --all-leagues

# refresh FotMob historical cache
python train_model.py --all-leagues --refresh-data

# quick/smaller training pass
python train_model.py --league premier_league --max-seasons 5
```

Training collects completed matches from FotMob league pages, caches them in `data/ml_matches.json`, builds pre-match rolling features, trains a 3-class home/draw/away classifier, and saves:

```text
data/match_model.joblib
data/model_meta.json
```

`data/` is gitignored; do not commit scraped datasets or trained model artifacts.

Features are built only from matches that happened before the target match:

- rolling goals for/against
- recent points per match
- home/away split form
- rest days
- league average goals up to that date
- attack/defense strength relative to league average
- pre-match Elo ratings
- season progress and home advantage

The model does not use final table position, final season stats, future matches, or betting odds.

### Poisson fallback

The fallback is an independent bivariate Poisson model. For each upcoming fixture it uses recent completed league matches to compute average goals-for and goals-against, then:

```
λ_home = ((home.gf + away.ga) / 2) × 1.15   # 1.15 = home advantage
λ_away =  (away.gf + home.ga) / 2
```

Then it enumerates all scorelines `(h, a)` for `h, a ∈ [0, 7]` and picks the one with the highest joint PMF. Win/draw/win probabilities come from summing across all matching scorelines.

One subtle thing: the most probable individual scoreline (say 1–1) can differ from the most probable outcome (Home Win), because "home win" aggregates 1–0 + 2–0 + 2–1 + 3–0 + … The `outcome` field uses the probability distribution, not the scoreline.

The ML model predicts W/D/L probabilities. The displayed projected score still comes from the Poisson baseline, so the bot can show both a scoreline and ML outcome probabilities.

Validation metrics printed by `train_model.py` include chronological validation accuracy, log loss when available, class distribution, and a naive home-win baseline. These predictions are probabilistic estimates, not guarantees.

**Leagues:**

| key | league | FotMob ID |
|---|---|---|
| `premier_league` | Premier League | 47 |
| `la_liga` | La Liga | 87 |
| `serie_a` | Serie A | 55 |
| `bundesliga` | Bundesliga | 54 |
| `ligue_1` | Ligue 1 | 53 |
| `liga_betplay` | Liga BetPlay Dimayor | 241 |

---

## Bulk match import

```python
from bulk_matches import bulk_import_matches

results = bulk_import_matches(
    urls=["https://www.fotmob.com/matches/..."],
    provider="fotmob",
    workers=2,
    delay=1.0,
    progress_cb=lambda r: print(r.status, r.home_team, r.score, r.away_team),
)
```

`MatchImportResult` fields: `url`, `status` ("ok" / "error" / "not_supported"), `match_id`, `home_team`, `away_team`, `score`, `date`, `error`.

### Provider status

| Provider | Status | Notes |
|---|---|---|
| `fotmob` | ✅ Supported | wraps existing `fotmob/scraper.py` functions |
| `sofascore` | 🚧 Planned | stub exists in `fotmob/providers/sofascore.py`; see that file for the implementation guide |

To add a new provider: create `fotmob/providers/<name>.py` with a `fetch_match(url) -> dict` function, add it to `PROVIDERS` and `_ENABLED` in `fotmob/providers/__init__.py`.

---

## Database

Seven tables. The first four cascade-delete from `players`; the last three cascade-delete from `imported_matches`.

**Player tables**
```sql
players      — id (FotMob PK), slug, name, position, club, fetched_at, ...
season_stats — player_id, label, value (TEXT)
career       — player_id, team, start_date, end_date, appearances, goals, assists (all TEXT)
matches      — player_id, match_date, fixture, score, result, mins, goals, assists, rating, motm, url
```

**Imported match tables**
```sql
imported_matches        — id, source, source_match_id (UNIQUE per source), match_url,
                          match_date, league, home_team, away_team, home_id, away_id,
                          score, home_formation, away_formation, fetched_at
imported_match_players  — imported_match_id, side, player_id, name, shirt, starter,
                          rating, x_norm, y_norm, goals, assists, yellow, red, motm,
                          subbed_on, subbed_off
imported_match_events   — imported_match_id, event_type, minute, player, team, detail
```

`appearances/goals/assists` in `career` are TEXT because FotMob returns things like `"123*"` for estimated figures. Cast to INT in SQL if you need aggregates.

On refresh, `upsert_player()` and `upsert_imported_match()` delete and re-insert all child rows. Full replace is simpler and fast enough given the data volumes.

---

## Discord commands

| command | description |
|---|---|
| `/player <name>` | profile, form strip, last 5 matches |
| `/stats <name>` | season stats with xG/xA |
| `/matches <name> [count]` | full match log |
| `/match <name> [number]` | lineup image + key events |
| `/career <name>` | club history with totals |
| `/compare <p1> <p2>` | head-to-head |
| `/predict <league>` | upcoming match predictions |
| `/fotmob_help` | lists commands |

Commands check the local DB first, fall back to a live scrape if the player isn't cached. All blocking I/O runs in a thread pool via `run_in_executor` so the event loop stays clean.

Slash commands sync globally on startup — takes up to an hour to propagate to all servers.
