"""
ml_predictor.py
---------------
Offline-trained match outcome model for FotMob league fixtures.

The runtime path is intentionally optional: if model artifacts or ML
dependencies are missing, callers should fall back to predictor.py's Poisson
model. Training is done by train_model.py, not by the Discord bot.
"""

from __future__ import annotations

import json
import logging
import math
import pickle
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fotmob.scraper import SESSION_HEADERS, make_session

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
MATCH_CACHE_PATH = DATA_DIR / "ml_matches.json"
MODEL_PATH = DATA_DIR / "match_model.joblib"
META_PATH = DATA_DIR / "model_meta.json"

FEATURE_VERSION = 1
LABELS = ["H", "D", "A"]
OUTCOME_NAMES = {"H": "Home Win", "D": "Draw", "A": "Away Win"}

FEATURE_NAMES = [
    "home_elo",
    "away_elo",
    "elo_diff",
    "home_matches_played",
    "away_matches_played",
    "home_recent_gf",
    "home_recent_ga",
    "away_recent_gf",
    "away_recent_ga",
    "home_recent_points",
    "away_recent_points",
    "home_home_gf",
    "home_home_ga",
    "away_away_gf",
    "away_away_ga",
    "home_attack_strength",
    "home_defense_strength",
    "away_attack_strength",
    "away_defense_strength",
    "league_avg_goals",
    "home_rest_days",
    "away_rest_days",
    "season_progress",
    "home_advantage",
]


def _optional_joblib():
    try:
        import joblib
        return joblib
    except ImportError:
        return None


def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value[:20] if "T" in value else value[:10], fmt)
        except ValueError:
            pass
    return None


def _score_from_match(m: dict) -> tuple[int, int] | None:
    hs, away_score = m.get("homeScore"), m.get("awayScore")
    if hs is None or away_score is None:
        score_str = (m.get("status") or {}).get("scoreStr", "")
        match = re.search(r"(\d+)\s*[-–]\s*(\d+)", score_str)
        if not match:
            return None
        hs, away_score = match.group(1), match.group(2)
    try:
        return int(hs), int(away_score)
    except (TypeError, ValueError):
        return None


def _extract_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        raise ValueError("FotMob page did not contain __NEXT_DATA__")
    data = json.loads(match.group(1))
    return (data.get("props") or {}).get("pageProps") or {}


def _league_page_props(session, league: dict, season: str | None = None) -> dict:
    url = f"https://www.fotmob.com/leagues/{league['id']}/overview/{league['slug']}"
    if season:
        url += f"?season={quote(season)}"
    r = session.get(url, headers=SESSION_HEADERS, timeout=20)
    r.raise_for_status()
    return _extract_next_data(r.text)


def _all_matches(page_props: dict) -> list[dict]:
    return (
        (page_props.get("matches") or {}).get("allMatches")
        or (page_props.get("fixtures") or {}).get("allMatches")
        or []
    )


def _completed_matches_from_page(page_props: dict, league_key: str, league: dict, season: str) -> list[dict]:
    rows = []
    for m in _all_matches(page_props):
        status = m.get("status") or {}
        if not status.get("finished") or status.get("cancelled"):
            continue
        score = _score_from_match(m)
        if not score:
            continue
        home = m.get("home") or {}
        away = m.get("away") or {}
        if not home.get("id") or not away.get("id"):
            continue
        utc = status.get("utcTime") or m.get("time") or ""
        date = utc[:10] if utc else ""
        hg, ag = score
        rows.append({
            "match_id": str(m.get("id") or ""),
            "league_key": league_key,
            "league_id": int(league["id"]),
            "season": season,
            "date": date,
            "round": m.get("roundName") or m.get("round") or 0,
            "home_team_id": int(home["id"]),
            "home_team": home.get("name", "?"),
            "away_team_id": int(away["id"]),
            "away_team": away.get("name", "?"),
            "home_goals": hg,
            "away_goals": ag,
            "result": "H" if hg > ag else "A" if ag > hg else "D",
        })
    return rows


def load_cached_matches() -> list[dict]:
    if not MATCH_CACHE_PATH.exists():
        return []
    return json.loads(MATCH_CACHE_PATH.read_text(encoding="utf-8"))


def save_cached_matches(matches: list[dict]):
    _ensure_data_dir()
    MATCH_CACHE_PATH.write_text(
        json.dumps(matches, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def collect_historical_matches(
    league_keys: list[str],
    leagues: dict[str, dict],
    refresh: bool = False,
    max_seasons: int | None = None,
    delay: float = 0.7,
) -> list[dict]:
    """Collect completed matches from FotMob league pages and cache them."""
    cached = [] if refresh else load_cached_matches()
    existing = {
        (m.get("league_key"), m.get("season"), str(m.get("match_id")))
        for m in cached
    }
    collected = list(cached)
    session = make_session()

    for league_key in league_keys:
        league = leagues[league_key]
        first_page = _league_page_props(session, league)
        seasons = first_page.get("allAvailableSeasons") or [
            (first_page.get("details") or {}).get("selectedSeason")
        ]
        seasons = [s for s in seasons if s]
        if max_seasons:
            seasons = seasons[:max_seasons]

        for idx, season in enumerate(seasons):
            try:
                page = first_page if idx == 0 else _league_page_props(session, league, season)
                rows = _completed_matches_from_page(page, league_key, league, season)
                added = 0
                for row in rows:
                    key = (row["league_key"], row["season"], str(row["match_id"]))
                    if key not in existing:
                        collected.append(row)
                        existing.add(key)
                        added += 1
                logger.info("Collected %s new matches for %s %s", added, league_key, season)
            except Exception:
                logger.exception("Failed collecting %s season %s", league_key, season)
            time.sleep(delay)

    collected.sort(key=lambda r: (r.get("date") or "", r.get("league_id") or 0, r.get("match_id") or ""))
    save_cached_matches(collected)
    return collected


def _avg(values: list[float], default: float = 0.0) -> float:
    return round(sum(values) / len(values), 4) if values else default


def _team_defaults(state: dict, league_totals: dict) -> dict:
    total_matches = max(1, league_totals["matches"])
    league_avg_goals = league_totals["goals"] / (2 * total_matches)
    matches_played = max(1, state["played"])
    return {
        "recent_gf": league_avg_goals,
        "recent_ga": league_avg_goals,
        "recent_points": 1.2,
        "home_gf": league_avg_goals,
        "home_ga": league_avg_goals,
        "away_gf": league_avg_goals,
        "away_ga": league_avg_goals,
        "attack_strength": (state["gf"] / matches_played) / max(0.1, league_avg_goals),
        "defense_strength": (state["ga"] / matches_played) / max(0.1, league_avg_goals),
    }


def _rolling_features(team_state: dict, side: str, league_totals: dict) -> dict:
    defaults = _team_defaults(team_state, league_totals)
    recent = list(team_state["recent"])
    side_recent = list(team_state[f"{side}_recent"])
    return {
        "recent_gf": _avg([x[0] for x in recent], defaults["recent_gf"]),
        "recent_ga": _avg([x[1] for x in recent], defaults["recent_ga"]),
        "recent_points": _avg([x[2] for x in recent], defaults["recent_points"]),
        f"{side}_gf": _avg([x[0] for x in side_recent], defaults[f"{side}_gf"]),
        f"{side}_ga": _avg([x[1] for x in side_recent], defaults[f"{side}_ga"]),
        "attack_strength": defaults["attack_strength"],
        "defense_strength": defaults["defense_strength"],
    }


def _new_team_state():
    return {
        "played": 0,
        "gf": 0,
        "ga": 0,
        "points": 0,
        "elo": 1500.0,
        "last_date": None,
        "recent": deque(maxlen=8),
        "home_recent": deque(maxlen=8),
        "away_recent": deque(maxlen=8),
    }


def _expected_score(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + math.pow(10, (elo_b - elo_a) / 400))


def _update_elo(home_state: dict, away_state: dict, result: str):
    home_score = 1.0 if result == "H" else 0.5 if result == "D" else 0.0
    expected_home = _expected_score(home_state["elo"] + 55, away_state["elo"])
    delta = 22 * (home_score - expected_home)
    home_state["elo"] += delta
    away_state["elo"] -= delta


def build_feature_rows(matches: list[dict], for_training: bool = True) -> tuple[list[list[float]], list[str], list[dict]]:
    """Build chronological pre-match features without future leakage."""
    rows = sorted(matches, key=lambda r: (r.get("date") or "", r.get("league_id") or 0, r.get("match_id") or ""))
    team_states = defaultdict(_new_team_state)
    league_totals = defaultdict(lambda: {"matches": 0, "goals": 0})
    season_counts = defaultdict(int)
    season_seen = defaultdict(int)
    for row in rows:
        season_counts[(row["league_key"], row["season"])] += 1

    x_rows: list[list[float]] = []
    y_rows: list[str] = []
    meta_rows: list[dict] = []

    for row in rows:
        league_key = row["league_key"]
        home_id, away_id = row["home_team_id"], row["away_team_id"]
        home_state = team_states[(league_key, home_id)]
        away_state = team_states[(league_key, away_id)]
        totals = league_totals[league_key]
        league_avg_goals = totals["goals"] / max(1, 2 * totals["matches"])

        home_roll = _rolling_features(home_state, "home", totals)
        away_roll = _rolling_features(away_state, "away", totals)
        match_date = _parse_date(row.get("date", ""))
        home_rest = (match_date - home_state["last_date"]).days if match_date and home_state["last_date"] else 7
        away_rest = (match_date - away_state["last_date"]).days if match_date and away_state["last_date"] else 7
        season_key = (league_key, row["season"])
        progress = season_seen[season_key] / max(1, season_counts[season_key])

        features = [
            home_state["elo"],
            away_state["elo"],
            home_state["elo"] - away_state["elo"],
            home_state["played"],
            away_state["played"],
            home_roll["recent_gf"],
            home_roll["recent_ga"],
            away_roll["recent_gf"],
            away_roll["recent_ga"],
            home_roll["recent_points"],
            away_roll["recent_points"],
            home_roll["home_gf"],
            home_roll["home_ga"],
            away_roll["away_gf"],
            away_roll["away_ga"],
            home_roll["attack_strength"],
            home_roll["defense_strength"],
            away_roll["attack_strength"],
            away_roll["defense_strength"],
            league_avg_goals,
            min(max(home_rest, 0), 30),
            min(max(away_rest, 0), 30),
            progress,
            1.0,
        ]
        x_rows.append(features)
        y_rows.append(row["result"])
        meta_rows.append(row)

        hg, ag = row["home_goals"], row["away_goals"]
        hp = 3 if hg > ag else 1 if hg == ag else 0
        ap = 3 if ag > hg else 1 if hg == ag else 0
        home_state["played"] += 1
        away_state["played"] += 1
        home_state["gf"] += hg
        home_state["ga"] += ag
        away_state["gf"] += ag
        away_state["ga"] += hg
        home_state["points"] += hp
        away_state["points"] += ap
        home_state["recent"].append((hg, ag, hp))
        away_state["recent"].append((ag, hg, ap))
        home_state["home_recent"].append((hg, ag, hp))
        away_state["away_recent"].append((ag, hg, ap))
        if match_date:
            home_state["last_date"] = match_date
            away_state["last_date"] = match_date
        _update_elo(home_state, away_state, row["result"])
        totals["matches"] += 1
        totals["goals"] += hg + ag
        season_seen[season_key] += 1

    return x_rows, y_rows, meta_rows


def _choose_model():
    try:
        from xgboost import XGBClassifier
        return "xgboost", XGBClassifier(
            n_estimators=180,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="multi:softprob",
            eval_metric="mlogloss",
            random_state=42,
        )
    except ImportError:
        pass

    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return "sklearn_hist_gradient_boosting", HistGradientBoostingClassifier(
            max_iter=180,
            learning_rate=0.05,
            random_state=42,
        )
    except ImportError:
        pass

    try:
        from sklearn.linear_model import LogisticRegression
        return "sklearn_logistic_regression", LogisticRegression(
            max_iter=1000,
            multi_class="auto",
        )
    except ImportError:
        pass

    raise ImportError(
        "No ML backend is installed. Install optional dependencies with: "
        "pip install xgboost scikit-learn pandas numpy joblib"
    )


def _encode_labels(labels: list[str]) -> list[int]:
    return [LABELS.index(y) for y in labels]


def _probability_rows(model, x_rows):
    if hasattr(model, "predict_proba"):
        return model.predict_proba(x_rows)
    preds = model.predict(x_rows)
    return [[1.0 if LABELS[int(p)] == label else 0.0 for label in LABELS] for p in preds]


def train_and_save(matches: list[dict], league_keys: list[str] | None = None) -> dict:
    """Train a chronological 3-class outcome model and save artifacts."""
    if league_keys:
        matches = [m for m in matches if m.get("league_key") in set(league_keys)]
    x_rows, y_labels, meta_rows = build_feature_rows(matches)
    if len(x_rows) < 80:
        raise ValueError(f"Need at least 80 completed matches to train; found {len(x_rows)}")

    split = max(1, int(len(x_rows) * 0.8))
    if len(x_rows) - split < 10:
        split = max(1, len(x_rows) - 10)

    backend, model = _choose_model()
    y_encoded = _encode_labels(y_labels)
    model.fit(x_rows[:split], y_encoded[:split])

    probs = _probability_rows(model, x_rows[split:])
    pred_labels = [LABELS[max(range(len(p)), key=lambda i: p[i])] for p in probs]
    actual = y_labels[split:]
    accuracy = sum(1 for p, y in zip(pred_labels, actual) if p == y) / max(1, len(actual))
    naive_accuracy = sum(1 for y in actual if y == "H") / max(1, len(actual))
    log_loss = None
    try:
        from sklearn.metrics import log_loss as _log_loss
        log_loss = float(_log_loss(_encode_labels(actual), probs, labels=[0, 1, 2]))
    except Exception:
        pass

    artifact = {
        "model": model,
        "backend": backend,
        "feature_names": FEATURE_NAMES,
        "feature_version": FEATURE_VERSION,
        "labels": LABELS,
    }
    _ensure_data_dir()
    joblib = _optional_joblib()
    if joblib:
        joblib.dump(artifact, MODEL_PATH)
    else:
        with MODEL_PATH.open("wb") as f:
            pickle.dump(artifact, f)

    class_counts = {label: y_labels.count(label) for label in LABELS}
    meta = {
        "backend": backend,
        "feature_names": FEATURE_NAMES,
        "feature_version": FEATURE_VERSION,
        "labels": LABELS,
        "trained_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "train_matches": split,
        "validation_matches": len(x_rows) - split,
        "total_matches": len(x_rows),
        "league_keys": sorted(set(m.get("league_key") for m in matches)),
        "accuracy": round(accuracy, 4),
        "log_loss": round(log_loss, 4) if log_loss is not None else None,
        "naive_home_accuracy": round(naive_accuracy, 4),
        "class_counts": class_counts,
        "last_match_date": meta_rows[-1].get("date") if meta_rows else None,
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def load_model_artifact() -> dict | None:
    if not MODEL_PATH.exists():
        return None
    joblib = _optional_joblib()
    try:
        if joblib:
            return joblib.load(MODEL_PATH)
        with MODEL_PATH.open("rb") as f:
            return pickle.load(f)
    except Exception:
        logger.exception("Failed loading ML model artifact")
        return None


def load_model_meta() -> dict:
    if not META_PATH.exists():
        return {}
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def has_trained_model() -> bool:
    return MODEL_PATH.exists()


def _state_after_matches(matches: list[dict]):
    x_rows, y_rows, meta_rows = build_feature_rows(matches)
    return x_rows, y_rows, meta_rows


def _feature_for_fixture(fixture: dict, history: list[dict]) -> list[float]:
    pseudo = dict(fixture)
    pseudo.update({
        "league_key": fixture["league_key"],
        "league_id": fixture["league_id"],
        "season": fixture.get("season") or "upcoming",
        "home_team_id": fixture["home_team_id"],
        "away_team_id": fixture["away_team_id"],
        "home_goals": 0,
        "away_goals": 0,
        "result": "D",
    })
    x_rows, _, _ = build_feature_rows(history + [pseudo])
    return x_rows[-1]


def get_ml_predictions(league_key: str, leagues: dict[str, dict], poisson_result: dict | None = None) -> dict:
    """Return ML predictions in the same shape as predictor.get_predictions()."""
    artifact = load_model_artifact()
    if not artifact:
        return {"error": "No trained ML model found", "predictions": []}

    from fotmob.predictor import _fetch_fixtures

    league = leagues.get(league_key)
    if not league:
        return {"error": f"Unknown league: {league_key}", "predictions": []}

    session = make_session()
    fixtures, league_matches, fetch_error = _fetch_fixtures(session, league)
    if not fixtures:
        return {
            "league": league,
            "predictions": [],
            "error": fetch_error or "No upcoming fixtures found.",
        }

    cached = load_cached_matches()
    history = [m for m in cached if m.get("league_key") == league_key]
    if not history:
        history = [
            _completed_matches_from_page({"fixtures": {"allMatches": league_matches}}, league_key, league, "current")
        ][0]

    model = artifact["model"]
    labels = artifact.get("labels") or LABELS
    poisson_by_id = {}
    if poisson_result:
        poisson_by_id = {str(p.get("match_id")): p for p in poisson_result.get("predictions", [])}

    rows = []
    fixture_meta = []
    for fix in fixtures:
        row = {
            "match_id": str(fix.get("match_id") or ""),
            "league_key": league_key,
            "league_id": league["id"],
            "season": "upcoming",
            "date": fix.get("date") or "",
            "round": 0,
            "home_team_id": fix["home_id"],
            "home_team": fix["home"],
            "away_team_id": fix["away_id"],
            "away_team": fix["away"],
        }
        rows.append(_feature_for_fixture(row, history))
        fixture_meta.append(fix)

    probs = _probability_rows(model, rows)
    predictions = []
    for fix, prob in zip(fixture_meta, probs):
        prob = [float(x) for x in prob]
        best_idx = max(range(len(prob)), key=lambda i: prob[i])
        label = labels[best_idx]
        p_home, p_draw, p_away = prob[labels.index("H")], prob[labels.index("D")], prob[labels.index("A")]
        poisson = poisson_by_id.get(str(fix.get("match_id")), {})
        predictions.append({
            "match_id": fix["match_id"],
            "home": fix["home"],
            "away": fix["away"],
            "date": fix["date"],
            "time": fix["time"],
            "scoreline": poisson.get("scoreline", "—"),
            "outcome": OUTCOME_NAMES.get(label, label),
            "confidence": round(prob[best_idx] * 100, 1),
            "p_home": round(p_home * 100, 1),
            "p_draw": round(p_draw * 100, 1),
            "p_away": round(p_away * 100, 1),
            "xg_home": poisson.get("xg_home", "—"),
            "xg_away": poisson.get("xg_away", "—"),
            "model_type": artifact.get("backend", "ml"),
        })

    meta = load_model_meta()
    return {
        "league": league,
        "predictions": predictions,
        "model_type": artifact.get("backend", "ml"),
        "model_meta": meta,
    }
