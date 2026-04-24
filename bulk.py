"""
bulk.py
-------
Bulk-scrape multiple FotMob players and persist them to PostgreSQL.

Each player name is resolved via the search API, then scraped in a thread
pool. A configurable delay between each worker's requests keeps FotMob
from rate-limiting the scraper.

Usage:
    # Names as arguments
    python bulk.py "erling haaland" "bruno fernandes" "kylian mbappe"

    # Names from a text file (one per line, # lines ignored)
    python bulk.py --file players.txt

    # Tune concurrency and delay
    python bulk.py --workers 2 --delay 1.5 --file players.txt

Output:
    Prints a summary table and exits with code 1 if any player failed.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from db import upsert_player
from scraper import fetch_player_json, make_session, parse_player, search_players


# ──────────────────────────────────────────────
# Result tracking
# ──────────────────────────────────────────────

@dataclass
class ScrapeResult:
    name:    str
    status:  str          # "ok" | "not_found" | "error"
    player:  Optional[dict] = None
    error:   str = ""
    matches: int = field(init=False, default=0)

    def __post_init__(self):
        if self.player:
            self.matches = len(self.player.get("matches") or [])


# ──────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────

def _scrape_one(name: str, delay: float) -> ScrapeResult:
    """Search → fetch → parse → upsert for a single player name."""
    time.sleep(delay)   # stagger workers to avoid burst requests
    try:
        hits = search_players(name)
        if not hits:
            return ScrapeResult(name=name, status="not_found",
                                error="No results from search API")
        # Take the top result
        top = hits[0]
        session = make_session()
        raw = fetch_player_json(session, top["id"], top["slug"])
        player = parse_player(raw)
        upsert_player(player)
        return ScrapeResult(name=name, status="ok", player=player)
    except Exception as exc:
        return ScrapeResult(name=name, status="error", error=str(exc))


# ──────────────────────────────────────────────
# Public API (used by app.py)
# ──────────────────────────────────────────────

def bulk_scrape(
    names: list[str],
    workers: int = 3,
    delay: float = 1.0,
    progress_cb=None,
) -> list[ScrapeResult]:
    """
    Scrape a list of player names concurrently.

    Args:
        names:       List of player name strings.
        workers:     Thread pool size (keep <= 3 to be polite to FotMob).
        delay:       Seconds each worker sleeps before its first request.
        progress_cb: Optional callable(result) called as each player finishes.

    Returns:
        List of ScrapeResult in completion order.
    """
    results = []
    # Stagger workers: worker i sleeps i * delay so they don't all hit at once
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_scrape_one, name, i * delay): name
            for i, name in enumerate(names)
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if progress_cb:
                progress_cb(result)
    return results


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def _load_names_from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.startswith("#")
        ]


def _print_result(r: ScrapeResult):
    icon = {"ok": "✓", "not_found": "?", "error": "✗"}[r.status]
    if r.status == "ok":
        p = r.player
        print(f"  {icon} {p['name']:<28} {p.get('club') or '':<25} {r.matches} matches")
    else:
        print(f"  {icon} {r.name:<28} {r.error}")


def main():
    parser = argparse.ArgumentParser(description="Bulk-scrape FotMob players")
    parser.add_argument("names", nargs="*", help="Player names to scrape")
    parser.add_argument("--file",    metavar="PATH",
                        help="Text file with one player name per line")
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel workers (default: 3)")
    parser.add_argument("--delay",   type=float, default=1.0,
                        help="Seconds between each worker's first request (default: 1.0)")
    args = parser.parse_args()

    names = list(args.names)
    if args.file:
        names += _load_names_from_file(args.file)

    if not names:
        parser.error("Provide player names as arguments or via --file")

    print(f"\nScraping {len(names)} player(s) with {args.workers} worker(s) "
          f"and {args.delay}s delay...\n")

    results = bulk_scrape(names, workers=args.workers, delay=args.delay,
                          progress_cb=_print_result)

    ok      = sum(1 for r in results if r.status == "ok")
    failed  = len(results) - ok
    total_m = sum(r.matches for r in results)

    print(f"\n{'─' * 50}")
    print(f"  Done: {ok} saved, {failed} failed, {total_m} total matches stored")
    print(f"{'─' * 50}\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
