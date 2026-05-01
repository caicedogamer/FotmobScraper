"""
train_model.py
--------------
Collect FotMob historical league matches and train the optional ML predictor.

Examples:
    python train_model.py --league premier_league
    python train_model.py --all-leagues
    python train_model.py --all-leagues --refresh-data
    python train_model.py --league premier_league --max-seasons 5
"""

import argparse
import json
import logging
import sys

from fotmob.predictor import LEAGUES
from fotmob.ml_predictor import (
    collect_historical_matches,
    load_cached_matches,
    train_and_save,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Train FotMob match outcome ML model")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--league", choices=list(LEAGUES), help="Train with one league")
    group.add_argument("--all-leagues", action="store_true", help="Train with all supported leagues")
    parser.add_argument("--refresh-data", action="store_true", help="Re-fetch historical matches")
    parser.add_argument("--max-seasons", type=int, default=None,
                        help="Limit seasons per league while collecting")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Use cached data only; fail if cache is empty")
    args = parser.parse_args()

    league_keys = list(LEAGUES) if args.all_leagues else [args.league]

    if args.no_fetch:
        matches = load_cached_matches()
    else:
        matches = collect_historical_matches(
            league_keys,
            LEAGUES,
            refresh=args.refresh_data,
            max_seasons=args.max_seasons,
        )

    matches = [m for m in matches if m.get("league_key") in set(league_keys)]
    if not matches:
        parser.error("No historical matches available. Re-run without --no-fetch.")

    try:
        meta = train_and_save(matches, league_keys=league_keys)
    except ImportError as exc:
        print(f"\nML dependencies missing: {exc}", file=sys.stderr)
        print("Install with: pip install xgboost scikit-learn pandas numpy joblib", file=sys.stderr)
        sys.exit(1)

    print("\nTraining complete")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
