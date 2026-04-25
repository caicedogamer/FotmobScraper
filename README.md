# FotMob Scraper

Scrapes player stats from FotMob, stores them in PostgreSQL, and serves them through a Flask UI and Discord bot. Also predicts upcoming match scores using a Poisson model.

---

## What's in the box

```
scraper.py    — HTTP scraping, buildId extraction, response parsing
db.py         — PostgreSQL layer
app.py        — Flask web app (port 5000)
bot.py        — Discord slash commands
bulk.py       — concurrent multi-player scraper
predictor.py  — Poisson match predictor for 6 leagues
pitch.py      — lineup image renderer (Pillow)
schema.sql    — DDL if you want to run it manually
```

---

## Setup

**Requirements:** Python 3.11+, PostgreSQL 14+, a Discord bot token if you want the bot.

```bash
pip install requests flask psycopg2-binary python-dotenv discord.py Pillow
```

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

`db.py` also runs `CREATE TABLE IF NOT EXISTS` on import, so the schema self-initialises if the DB already exists.

---

## Running

```bash
python app.py       # Flask on :5000
python bot.py       # Discord bot

# scrape one player from the CLI
python scraper.py 961995 erling-haaland
python scraper.py 961995 erling-haaland --raw   # dump raw JSON

# bulk scrape
python bulk.py "erling haaland" "kylian mbappe"
python bulk.py --file players.txt --workers 2 --delay 1.5
```

`players.txt`: one name per line, `#` lines are ignored.

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

## Bulk scraper

```python
bulk_scrape(names, workers=3, delay=1.0, progress_cb=None)
```

Workers are staggered: worker `i` sleeps `i * delay` seconds before its first request. This avoids the burst at `t=0` that gets you rate-limited. Each worker gets its own session (sharing sessions across threads isn't safe due to the cookie jar).

Keep `workers ≤ 3`. Beyond that FotMob starts returning 429s or empty responses.

The web UI at `/bulk` streams results back as SSE — the Flask route puts a thread on `bulk_scrape` and the generator drains a `queue.Queue` as players finish.

---

## Match predictor

`predictor.py` uses an independent bivariate Poisson model. For each upcoming fixture it fetches the last 8 results for both teams from FotMob's team API, computes average goals-for and goals-against, then:

```
λ_home = ((home.gf + away.ga) / 2) × 1.15   # 1.15 = home advantage
λ_away =  (away.gf + home.ga) / 2
```

Then it enumerates all scorelines `(h, a)` for `h, a ∈ [0, 7]` and picks the one with the highest joint PMF. Win/draw/win probabilities come from summing across all matching scorelines.

One subtle thing: the most probable individual scoreline (say 1–1) can differ from the most probable outcome (Home Win), because "home win" aggregates 1–0 + 2–0 + 2–1 + 3–0 + … The `outcome` field uses the probability distribution, not the scoreline.

Team form results are cached in memory for 1 hour. The first load for a league takes ~15 seconds (one API call per team); subsequent hits are instant.

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

## Database

Four tables. Child tables cascade-delete from `players`.

```sql
players      — id (FotMob PK), slug, name, position, club, fetched_at, ...
season_stats — player_id, label, value (TEXT)
career       — player_id, team, start_date, end_date, appearances, goals, assists (all TEXT)
matches      — player_id, match_date, fixture, score, result, mins, goals, assists, rating, motm, url
```

`appearances/goals/assists` in `career` are TEXT because FotMob returns things like `"123*"` for estimated figures. Cast to INT in SQL if you need aggregates.

On refresh, `upsert_player()` deletes and re-inserts all child rows. Match IDs aren't returned in the player JSON so there's no reliable key for partial updates — full replace is simpler and fast enough.

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
