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

import logging
import json
import math
import re
import time

from fotmob.scraper import JSON_HEADERS, SESSION_HEADERS, make_session

logger = logging.getLogger(__name__)

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
        logger.exception("Failed to fetch team form for team_id=%s", team_id)
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


def _all_matches(data: dict) -> list[dict]:
    """Return all league matches from either API JSON or pageProps JSON."""
    return (
        (data.get("matches") or {}).get("allMatches")
        or (data.get("fixtures") or {}).get("allMatches")
        or []
    )


def _normalise_fixtures(data: dict) -> list[dict]:
    """Return upcoming fixtures from either API JSON or pageProps JSON."""
    out: list[dict] = []
    for m in _all_matches(data):
        normalised = _normalise_fixture(m)
        if normalised:
            out.append(normalised)
        if len(out) >= 10:
            break
    return out


def _normalise_fixture(m: dict) -> dict | None:
    """Return one upcoming fixture dict, or None if the match should be skipped."""
    st = m.get("status") or {}
    if st.get("started") or st.get("finished") or st.get("cancelled"):
        return None

    home = m.get("home") or {}
    away = m.get("away") or {}
    hid, aid = home.get("id"), away.get("id")
    if not hid or not aid:
        return None

    utc = st.get("utcTime", "")
    try:
        hid, aid = int(hid), int(aid)
    except (TypeError, ValueError):
        return None

    return {
        "match_id": m.get("id"),
        "home_id":  hid,
        "away_id":  aid,
        "home":     home.get("name", "?"),
        "away":     away.get("name", "?"),
        "date":     utc[:10]   if utc           else "TBD",
        "time":     utc[11:16] if len(utc) > 15 else "",
    }


def _team_form_from_matches(all_matches: list[dict], team_id: int) -> dict:
    """Return {gf, ga} from the team's last 8 completed league matches."""
    raw: list[tuple[int, int]] = []
    for m in all_matches:
        st = m.get("status") or {}
        if not st.get("finished"):
            continue

        home = m.get("home") or {}
        away = m.get("away") or {}
        hid, aid = str(home.get("id")), str(away.get("id"))
        tid = str(team_id)
        if tid not in (hid, aid):
            continue

        hs, as_ = m.get("homeScore"), m.get("awayScore")
        if hs is None or as_ is None:
            score_str = st.get("scoreStr", "")
            score_match = re.search(r"(\d+)\s*[-–]\s*(\d+)", score_str)
            if not score_match:
                continue
            hs, as_ = int(score_match.group(1)), int(score_match.group(2))

        try:
            hs, as_ = int(hs), int(as_)
        except (TypeError, ValueError):
            continue

        raw.append((hs, as_) if tid == hid else (as_, hs))

    if not raw:
        return {"gf": 1.3, "ga": 1.1, "ts": time.time()}

    recent = raw[-8:]
    return {
        "gf": round(sum(x[0] for x in recent) / len(recent), 2),
        "ga": round(sum(x[1] for x in recent) / len(recent), 2),
        "ts": time.time(),
    }


def _fetch_league_page_data(session, league: dict) -> dict:
    """Fetch the league page and extract embedded Next.js pageProps."""
    url = (
        f"https://www.fotmob.com/leagues/{league['id']}/overview/"
        f"{league['slug']}"
    )
    r = session.get(url, headers=SESSION_HEADERS, timeout=15)
    r.raise_for_status()
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r.text,
        re.S,
    )
    if not match:
        raise ValueError("FotMob league page did not contain __NEXT_DATA__")
    data = json.loads(match.group(1))
    return (data.get("props") or {}).get("pageProps") or {}


def _fetch_fixtures(session, league: dict) -> tuple[list[dict], list[dict], str | None]:
    """Return the next upcoming fixtures for a league (at most 10)."""
    league_id = league["id"]

    # Older FotMob API path. Keep it first because it is cheaper when available.
    try:
        r = session.get(
            f"https://www.fotmob.com/api/leagues?id={league_id}",
            headers=JSON_HEADERS,
            timeout=12,
        )
        if r.status_code == 200 and "application/json" in r.headers.get("content-type", ""):
            data = r.json()
            fixtures = _normalise_fixtures(data)
            if fixtures:
                return fixtures, _all_matches(data), None
        else:
            logger.warning(
                "League API returned status=%s content-type=%s for league_id=%s; trying page data",
                r.status_code,
                r.headers.get("content-type"),
                league_id,
            )
    except Exception as exc:
        logger.warning("League API fetch failed for league_id=%s: %s", league_id, exc)

    # Current FotMob path: fixtures are embedded in the league page's pageProps.
    try:
        page_props = _fetch_league_page_data(session, league)
        return _normalise_fixtures(page_props), _all_matches(page_props), None
    except Exception as exc:
        logger.exception("Failed to fetch fixtures for league_id=%s", league_id)
        return [], [], str(exc)


# ── Public API ────────────────────────────────────────────────────────────────

def get_poisson_predictions(league_key: str) -> dict:
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

    session = make_session()
    fixtures, league_matches, fetch_error = _fetch_fixtures(session, league)

    if not fixtures:
        if fetch_error:
            return {
                "league":      league,
                "predictions": [],
                "error":       f"Could not fetch fixtures from FotMob: {fetch_error}",
            }
        return {
            "league":      league,
            "predictions": [],
            "error":       "No upcoming fixtures found — the season may be on a break.",
        }

    # Collect unique team IDs in encounter order (for deterministic stagger)
    seen_ids: set[int] = set()
    unique_team_ids: list[int] = []
    for fix in fixtures:
        for tid in (fix["home_id"], fix["away_id"]):
            if tid not in seen_ids:
                seen_ids.add(tid)
                unique_team_ids.append(tid)

    team_forms: dict[int, dict] = {
        tid: _team_form_from_matches(league_matches, tid)
        for tid in unique_team_ids
    }

    results: list[dict] = []
    for fix in fixtures:
        hf = team_forms.get(fix["home_id"]) or {"gf": 1.3, "ga": 1.1}
        af = team_forms.get(fix["away_id"]) or {"gf": 1.3, "ga": 1.1}

        lam_home = max(0.3, (hf["gf"] + af["ga"]) / 2 * HOME_ADVANTAGE)
        lam_away = max(0.3, (af["gf"] + hf["ga"]) / 2)

        pred = _predict(lam_home, lam_away)
        pred.update({
            "match_id": fix["match_id"],
            "home":     fix["home"],
            "away":     fix["away"],
            "date":     fix["date"],
            "time":     fix["time"],
            "model_type": "poisson",
        })
        results.append(pred)

    return {
        "league": league,
        "predictions": results,
        "model_type": "poisson",
        "model_meta": {"backend": "poisson", "total_matches": len(league_matches)},
    }


def get_predictions(league_key: str, model: str = "auto") -> dict:
    """
    Return predictions for a league.

    model:
      "poisson" - always use the built-in Poisson baseline.
      "ml"      - use a trained ML artifact when available, else fall back.
      "auto"    - prefer ML when a trained artifact exists, else Poisson.
    """
    if model not in {"auto", "ml", "poisson"}:
        return {"error": f"Unknown model mode: {model}", "predictions": []}

    poisson_result = get_poisson_predictions(league_key)
    if model == "poisson" or not poisson_result.get("predictions"):
        return poisson_result

    try:
        from fotmob.ml_predictor import get_ml_predictions, has_trained_model
        if model == "auto" and not has_trained_model():
            return poisson_result
        ml_result = get_ml_predictions(league_key, LEAGUES, poisson_result=poisson_result)
        if ml_result.get("predictions"):
            return ml_result
        if model == "ml":
            logger.warning("ML prediction unavailable, falling back to Poisson: %s", ml_result.get("error"))
        return poisson_result
    except Exception:
        logger.exception("ML prediction failed; falling back to Poisson")
        return poisson_result
