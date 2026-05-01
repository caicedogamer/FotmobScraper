"""
bulk_matches.py
---------------
Bulk-import match details from a provider (default: FotMob) and save them
to PostgreSQL.

Usage:
    # URLs as positional arguments
    python bulk_matches.py URL1 URL2 URL3

    # URLs from a text file (one per line, # lines and blank lines ignored)
    python bulk_matches.py --file matches.txt

    # Choose provider, concurrency, and delay
    python bulk_matches.py --provider fotmob --workers 2 --delay 1.0 --file matches.txt

Example matches.txt:
    # Premier League
    https://www.fotmob.com/matches/man-city-vs-arsenal/...
    https://www.fotmob.com/matches/chelsea-vs-liverpool/...

Output:
    Prints a summary table; exits with code 1 if any match failed.
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fotmob.db import init_db, upsert_imported_match
from fotmob.providers import PROVIDERS, fetch_match, is_enabled

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class MatchImportResult:
    url:       str
    status:    str             # "ok" | "error" | "not_supported"
    match_id:  Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    score:     Optional[str] = None
    date:      Optional[str] = None
    error:     str = ""


# ── Worker ────────────────────────────────────────────────────────────────────

def _import_one(
    url: str, provider: str, stagger: float, engine: str = "requests"
) -> MatchImportResult:
    """Fetch → normalise → persist one match URL."""
    if stagger > 0:
        time.sleep(stagger)
    try:
        match = fetch_match(url, provider=provider, engine=engine)
        upsert_imported_match(match, source=provider, match_url=url)
        return MatchImportResult(
            url=url,
            status="ok",
            match_id=str(match.get("match_id", "")),
            home_team=match.get("home_team"),
            away_team=match.get("away_team"),
            score=match.get("score"),
            date=match.get("date"),
        )
    except NotImplementedError as exc:
        logger.warning("Provider not implemented for %s: %s", url, exc)
        return MatchImportResult(url=url, status="not_supported", error=str(exc))
    except Exception as exc:
        logger.exception("Failed to import match %s", url)
        return MatchImportResult(url=url, status="error", error=str(exc))


# ── Public API ────────────────────────────────────────────────────────────────

def bulk_import_matches(
    urls: list[str],
    provider: str = "fotmob",
    workers: int = 2,
    delay: float = 1.0,
    progress_cb=None,
    engine: str = "requests",
) -> list[MatchImportResult]:
    """
    Import a list of match URLs concurrently.

    Args:
        urls:        Match page URLs to import.
        provider:    Provider name ("fotmob", etc.).
        workers:     Thread pool size — keep ≤ 3 to be polite to upstreams.
        delay:       Worker-stagger delay in seconds (worker i sleeps i*delay).
        progress_cb: Optional callable(MatchImportResult) called as each URL completes.
        engine:      Fetch backend: "requests" (default), "scrapling", or "auto".

    Returns:
        List of MatchImportResult in completion order.
    """
    results: list[MatchImportResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_import_one, url, provider, i * delay, engine): url
            for i, url in enumerate(urls)
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if progress_cb:
                progress_cb(result)
    return results


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _load_urls_from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.startswith("#")
        ]


def _print_result(r: MatchImportResult):
    icon = {"ok": "✓", "not_supported": "?", "error": "✗"}[r.status]
    if r.status == "ok":
        fix = f"{r.home_team or '?'} {r.score or '?'} {r.away_team or '?'}"
        print(f"  {icon} [{r.date or '?'}] {fix:<45}  id={r.match_id}")
    else:
        short_url = r.url[-60:] if len(r.url) > 60 else r.url
        print(f"  {icon} {short_url:<60}  {r.error}")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bulk-import FotMob (or other provider) matches")
    parser.add_argument("urls",      nargs="*", metavar="URL", help="Match page URLs to import")
    parser.add_argument("--file",    metavar="PATH",
                        help="Text file with one match URL per line")
    parser.add_argument("--provider", default="fotmob",
                        choices=list(PROVIDERS),
                        help="Data provider (default: fotmob)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Parallel workers, max 3 (default: 2)")
    parser.add_argument("--delay",   type=float, default=1.0,
                        help="Stagger delay between workers in seconds (default: 1.0)")
    parser.add_argument(
        "--engine", default="requests",
        choices=["requests", "scrapling", "auto"],
        help="Fetch backend: requests (default), scrapling, or auto (requests with Scrapling fallback)",
    )
    args = parser.parse_args()

    if not is_enabled(args.provider):
        parser.error(
            f"Provider {args.provider!r} is not yet implemented. "
            f"Enabled providers: {[p for p in PROVIDERS if is_enabled(p)]}"
        )

    urls = list(args.urls)
    if args.file:
        urls += _load_urls_from_file(args.file)

    if not urls:
        parser.error("Provide match URLs as arguments or via --file")

    workers = max(1, min(args.workers, 3))
    delay   = max(0.5, args.delay)

    init_db()

    print(f"\nImporting {len(urls)} match(es) via {args.provider} "
          f"with {workers} worker(s) and {delay}s delay...\n")

    results = bulk_import_matches(
        urls, provider=args.provider,
        workers=workers, delay=delay,
        progress_cb=_print_result,
        engine=args.engine,
    )

    ok             = sum(1 for r in results if r.status == "ok")
    not_supported  = sum(1 for r in results if r.status == "not_supported")
    failed         = sum(1 for r in results if r.status == "error")

    print(f"\n{'─' * 60}")
    print(f"  Done: {ok} saved, {not_supported} not supported, {failed} errors")
    print(f"{'─' * 60}\n")

    sys.exit(1 if (failed or not_supported) else 0)


if __name__ == "__main__":
    main()
