"""
predictor.py
------------
Match score predictor for the top 5 European leagues + Colombian Liga BetPlay.

Algorithm (Poisson model):
  For each team we compute avg goals-for (gf) and goals-against (ga)
  over their last 8 matches (fetched from FotMob's team API).

    λ_home = ((home.gf + away.ga) / 2) × HOME_ADVANTAGE
    λ_away =  (away.gf + home.ga) / 2

  We then enumerate the Poisson joint PMF over [0..7] × [0..7] to find
  the most probable scoreline and compute home/draw/away win probabilities.

Team form results are cached in memory for 1 hour to avoid hammering FotMob.
"""

import math
import time

from scraper import JSON_HEADERS, make_session

LEAGUES: dict[str, dict] = {
    "premier_league": {"id": 47,  "slug": "premier-league",      "name": "Premier League", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "la_liga":        {"id": 87,  "slug": "laliga",               "name": "La Liga",        "flag": "🇪🇸"},
    "serie_a":        {"id": 55,  "slug": "serie-a",              "name": "Serie A",        "flag": "🇮🇹"},
    "bundesliga":     {"id": 54,  "slug": "bundesliga",           "name": "Bundesliga",     "flag": "🇩🇪"},
    "ligue_1":        {"id": 53,  "slug": "ligue-1",              "name": "Ligue 1",        "flag": "🇫🇷"},
    "liga_betplay":   {"id": 241, "slug": "liga-betplay-dimayor", "name": "Liga BetPlay",   "flag": "🇨🇴"},
}

HOME_ADVANTAGE = 1.15
_CACHE_TTL = 3600  # 1 hour
_team_cache: dict[int, dict] = {}


# ── Poisson math ──────────────────────────────────────────────────────────────

def _pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _predict(lam_home: float, lam_away: float, cap: int = 7) -> dict:
    """Return the most likely scoreline + win/draw/win probabilities."""
    best_p = -1.0
    best = (1, 1)
    p_home_win = p_draw = p_away_win = 0.0

    for h in range(cap + 1):
        ph = _pmf(h, lam_home)
        for a in range(cap + 1):
            p = ph * _pmf(a, lam_away)
            if p > best_p:
                best_p, best = p, (h, a)
            if h > a:
                p_home_win += p
            elif h < a:
                p_away_win += p
            else:
                p_draw += p

    hg, ag = best
    # Outcome from win-probability distribution, not from the scoreline alone.
    # (e.g. 1-1 may be the modal score yet home wins sum to 51% overall.)
    if p_home_win >= p_draw and p_home_win >= p_away_win:
        outcome, conf = "Home Win", round(p_home_win * 100, 1)
    elif p_away_win >= p_draw and p_away_win > p_home_win:
        outcome, conf = "Away Win", round(p_away_win * 100, 1)
    else:
        outcome, conf = "Draw", round(p_draw * 100, 1)

    return {
        "scoreline":  f"{hg}–{ag}",
        "home_goals": hg,
        "away_goals": ag,
        "outcome":    outcome,
        "confidence": conf,
        "p_home":     round(p_home_win * 100, 1),
        "p_draw":     round(p_draw     * 100, 1),
        "p_away":     round(p_away_win * 100, 1),
        "xg_home":    round(lam_home, 2),
        "xg_away":    round(lam_away, 2),
    }


# ── FotMob data fetching ──────────────────────────────────────────────────────

def _team_form(session, team_id: int) -> dict:
    """Return {gf, ga} average over last 8 matches, cached 1 h."""
    now = time.time()
    hit = _team_cache.get(team_id)
    if hit and (now - hit.get("ts", 0)) < _CACHE_TTL:
        return hit

    fallback = {"gf": 1.3, "ga": 1.1, "ts": now}

    try:
        r = session.get(
            f"https://www.fotmob.com/api/teams?id={team_id}",
            headers=JSON_HEADERS,
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        _team_cache[team_id] = fallback
        return fallback

    raw: list[tuple[int, int]] = []

    # Primary path: recentResults.matches
    for m in (data.get("recentResults") or {}).get("matches") or []:
        hs, as_ = m.get("homeScore"), m.get("awayScore")
        if hs is None or as_ is None:
            continue
        home_id = (m.get("home") or {}).get("id")
        if str(home_id) == str(team_id):
            raw.append((hs, as_))
        else:
            raw.append((as_, hs))

    # Fallback path: fixtures.previousMatches
    if not raw:
        for m in (data.get("fixtures") or {}).get("previousMatches") or []:
            hs, as_ = m.get("homeScore"), m.get("awayScore")
            if hs is None or as_ is None:
                continue
            home_id = (m.get("home") or {}).get("id")
            if str(home_id) == str(team_id):
                raw.append((hs, as_))
            else:
                raw.append((as_, hs))

    if not raw:
        _team_cache[team_id] = fallback
        return fallback

    recent = raw[:8]
    result = {
        "gf": round(sum(x[0] for x in recent) / len(recent), 2),
        "ga": round(sum(x[1] for x in recent) / len(recent), 2),
        "ts": now,
    }
    _team_cache[team_id] = result
    return result


def _fetch_fixtures(session, league_id: int) -> list[dict]:
    """Return the next upcoming fixtures for a league (at most 10)."""
    try:
        r = session.get(
            f"https://www.fotmob.com/api/leagues?id={league_id}",
            headers=JSON_HEADERS,
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    out: list[dict] = []
    for m in (data.get("matches") or {}).get("allMatches") or []:
        st = m.get("status") or {}
        if st.get("started") or st.get("finished") or st.get("cancelled"):
            continue

        home = m.get("home") or {}
        away = m.get("away") or {}
        hid, aid = home.get("id"), away.get("id")
        if not hid or not aid:
            continue

        utc = st.get("utcTime", "")
        try:
            hid, aid = int(hid), int(aid)
        except (TypeError, ValueError):
            continue

        out.append({
            "match_id": m.get("id"),
            "home_id":  hid,
            "away_id":  aid,
            "home":     home.get("name", "?"),
            "away":     away.get("name", "?"),
            "date":     utc[:10]   if utc           else "TBD",
            "time":     utc[11:16] if len(utc) > 15 else "",
        })
        if len(out) >= 10:
            break

    return out


# ── Public API ────────────────────────────────────────────────────────────────

def get_predictions(league_key: str) -> dict:
    """
    Fetch upcoming fixtures + team form for a league, run Poisson predictions.
    Returns:
      {league, predictions: [...], error?: str}
    Each prediction includes: home, away, date, time, scoreline, outcome,
    confidence, p_home, p_draw, p_away, xg_home, xg_away.
    """
    league = LEAGUES.get(league_key)
    if not league:
        return {"error": f"Unknown league: {league_key}", "predictions": []}

    session  = make_session()
    fixtures = _fetch_fixtures(session, league["id"])

    if not fixtures:
        return {
            "league":      league,
            "predictions": [],
            "error":       "No upcoming fixtures found — the season may be on a break.",
        }

    seen: set[int] = set()
    results: list[dict] = []

    for fix in fixtures:
        hid, aid = fix["home_id"], fix["away_id"]

        if hid not in seen:
            hf = _team_form(session, hid)
            seen.add(hid)
            time.sleep(0.25)
        else:
            hf = _team_cache.get(hid, {"gf": 1.3, "ga": 1.1})

        if aid not in seen:
            af = _team_form(session, aid)
            seen.add(aid)
            time.sleep(0.25)
        else:
            af = _team_cache.get(aid, {"gf": 1.3, "ga": 1.1})

        lam_home = max(0.3, (hf["gf"] + af["ga"]) / 2 * HOME_ADVANTAGE)
        lam_away = max(0.3, (af["gf"] + hf["ga"]) / 2)

        pred = _predict(lam_home, lam_away)
        pred.update({
            "match_id": fix["match_id"],
            "home":     fix["home"],
            "away":     fix["away"],
            "date":     fix["date"],
            "time":     fix["time"],
        })
        results.append(pred)

    return {"league": league, "predictions": results}
