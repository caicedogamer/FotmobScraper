"""
providers/__init__.py
---------------------
Provider registry for match data sources.

Each provider module must expose a single function:

    fetch_match(url: str) -> dict

The returned dict must conform to this shape (same as scraper.parse_match):
    {
        match_id:        str | int | None,
        date:            str,          # YYYY-MM-DD
        league:          str,
        venue:           str,
        home_team:       str,
        away_team:       str,
        home_id:         str | int | None,
        away_id:         str | int | None,
        score:           str,          # e.g. "2–1"
        home_formation:  str,
        away_formation:  str,
        home_lineup:     list[dict],   # see imported_match_players schema
        away_lineup:     list[dict],
        events:          list[dict],   # {type, minute, player, team, detail}
    }

To add a new provider:
  1. Create providers/<name>.py with a fetch_match(url) function.
  2. Add an entry to PROVIDERS and _ENABLED below.
"""

# Human-readable display names used in UI dropdowns.
PROVIDERS: dict[str, str] = {
    "fotmob":    "FotMob",
    "sofascore": "Sofascore (coming soon)",
}

# Providers that are actually implemented and safe to call.
_ENABLED: frozenset[str] = frozenset({"fotmob"})


def is_enabled(provider: str) -> bool:
    """Return True if the provider is implemented and available."""
    return provider in _ENABLED


def fetch_match(url: str, provider: str = "fotmob", engine: str = "requests") -> dict:
    """
    Fetch and normalise a match from a URL using the named provider.

    Args:
        url:      Match page URL.
        provider: Provider name ("fotmob", etc.).
        engine:   Fetch backend — "requests" (default), "scrapling", or "auto".

    Raises:
        ValueError           — unknown provider name
        NotImplementedError  — provider is known but not yet implemented
        requests.HTTPError   — upstream HTTP failure
    """
    if provider == "fotmob":
        from fotmob.providers.fotmob import fetch_match as _fetch
        return _fetch(url, engine=engine)
    if provider == "sofascore":
        from fotmob.providers.sofascore import fetch_match as _fetch
        return _fetch(url, engine=engine)
    raise ValueError(f"Unknown provider: {provider!r}. Known providers: {list(PROVIDERS)}")
