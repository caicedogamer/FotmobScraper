# Fotmob — project context

## What this is
A Python web app + Discord bot that scrapes player stats from FotMob, displays them in a dark-themed Flask UI, and surfaces them in Discord via slash commands. Backed by PostgreSQL.

## File layout
```
app.py              Flask web app (entry point — python app.py, port 5000)
bot.py              Discord bot (entry point — python bot.py)
bulk.py             Bulk player scraper + CLI
bulk_matches.py     Bulk match importer + CLI
train_model.py      Offline ML model training CLI
scraper.py          Compatibility wrapper for the player scraper CLI
fotmob/scraper.py   HTTP scraping + response parsing (no DB knowledge)
fotmob/db.py        PostgreSQL layer (upsert_player, load_player, list_players)
fotmob/predictor.py Match predictor facade
fotmob/providers/   Match provider implementations
schema.sql          DDL — run step 1 against postgres DB, step 2 against fotmob DB
.env                DB credentials + DISCORD_TOKEN (gitignored)
.gitignore
CLAUDE.md
```

## Discord bot
`bot.py` — uses `discord.py` with slash commands (`app_commands`).

Slash commands:
| Command | Description |
|---|---|
| `/player <name>` | Full profile embed: bio, goal contributions, last 5 matches |
| `/stats <name>` | Season stats table with goal contributions highlighted |
| `/matches <name> [count]` | Recent match log (result, score, G, A, rating, MOTM) |
| `/career <name>` | Club career history with totals |
| `/compare <player1> <player2>` | Side-by-side goal contribution comparison |
| `/fotmob_help` | List all commands |

**Resolution logic:** Checks local DB first (fast path via `list_players`); if not found, scrapes live from FotMob.  
**Token:** Set `DISCORD_TOKEN` in `.env`  
**Run:** `python bot.py`  
**Dependency:** `pip install discord.py`

## How scraping works
FotMob is a Next.js site. Every deployment rotates a `buildId` embedded in the page HTML inside `__NEXT_DATA__`. The scraper:
1. Warms up the session against the homepage (picks up cookies)
2. GETs the player HTML page, regex-extracts `buildId`
3. Constructs `/_next/data/{buildId}/en/players/{id}/{slug}.json` and fetches it

**Critical:** do NOT set `Accept-Encoding: gzip, deflate, br` in SESSION_HEADERS. FotMob responds with Brotli when advertised, but `requests` cannot decode Brotli — the response arrives as garbage binary. Omit the header and `requests` negotiates gzip only.

## Data shape (after parse_player)
`pageProps.data.*` — top-level fields used:
- `name`, `id`, `primaryTeam.teamName`, `positionDescription.primaryPosition.label`
- `playerInformation` — list of `{title, value}` items; titles used: `"Country"`, `"Age"`, `"Shirt"`
- `mainLeague.stats` — list of `{title, value}` season stat rows
- `careerHistory.careerItems.senior.teamEntries` — club career list
- `recentMatches` — match list; `onBench: true` rows are skipped

Player image URL pattern: `https://images.fotmob.com/image_resources/playerimages/{id}.png`

## Database
PostgreSQL. Connection config via `.env` (loaded by `python-dotenv`):
```
PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD
```

Four tables — all child tables cascade-delete from `players`:

| Table | Key columns |
|---|---|
| `players` | `id` (FotMob ID, PK), `slug`, `fetched_at` |
| `season_stats` | `player_id`, `label`, `value` (TEXT) |
| `career` | `player_id`, `team`, `start_date`, `end_date`, `appearances/goals/assists` (TEXT) |
| `matches` | `player_id`, `match_date`, `fixture`, `result` (W/D/L), `motm` (BOOLEAN) |

`career.appearances/goals/assists` are TEXT because FotMob returns them as strings — cast to INTEGER in SQL if doing aggregates.

`upsert_player` does a full delete+re-insert of child rows on every refresh.

## Search
`search_players(term)` in `scraper.py` hits `https://apigw.fotmob.com/searchapi/suggest`.
Returns `[{id, name, slug, team}]`. Slug is derived from name via `name_to_slug()` (lowercase + hyphens) — FotMob's player URLs always follow this pattern.
The `/search?q=` endpoint in `app.py` proxies this for the frontend autocomplete.

## App behaviour
- Single search box with live dropdown (debounced 300 ms, min 2 chars) — no ID/slug needed
- Selecting a result navigates to `/?player_id=&slug=`, fetches data, saves to DB
- Sidebar lists all players stored in DB; click to reload from DB instantly
- "Refresh" link beside the cache note forces a re-fetch from FotMob

## Bulk scraping
`bulk.py` — scrapes many players concurrently and saves all to the DB.

**Thread pool design:**
- `bulk_scrape(names, workers=3, delay=1.0, progress_cb=None)` is the public API
- Workers are staggered: worker `i` sleeps `i * delay` seconds before its first request, preventing request bursts
- Each worker: `search_players(name)` → top result → `make_session()` → `fetch_player_json()` → `parse_player()` → `upsert_player()`
- `progress_cb(ScrapeResult)` is called as each player completes (used by the app for SSE streaming)
- Keep `workers <= 3` to avoid rate-limiting from FotMob

**CLI:**
```bash
python bulk.py "erling haaland" "kylian mbappe"
python bulk.py --file players.txt --workers 2 --delay 1.5
```
File format: one name per line, lines starting with `#` are ignored.

**Web UI:** `/bulk` — textarea for names, worker/delay controls, live progress table via SSE.
`/bulk/stream` (POST, JSON) runs `bulk_scrape` in a background thread and streams `data:` SSE events back as each player finishes.

## Running
```bash
python app.py        # Flask dev server on :5000 (threaded=True required for SSE)
python scraper.py    # CLI scrape (Bruno Fernandes default)
python bulk.py --file players.txt
```

## Dependencies
```
requests
flask
psycopg2-binary
python-dotenv
```
