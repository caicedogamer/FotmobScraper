"""
providers/fotmob.py
-------------------
FotMob match provider.

Wraps scraper.fetch_match_json + scraper.parse_match so that the rest of the
app can call providers.fetch_match(url, provider="fotmob") without importing
scraper directly.  scraper.py imports in bot.py and elsewhere are unchanged.
"""

from fotmob.scraper import fetch_match_json, make_session
from fotmob.scraper import parse_match as _parse_match


def fetch_match(url: str, engine: str = "requests") -> dict:
    """
    Fetch a FotMob match page by URL and return a normalised match dict.

    Accepted URL forms:
      https://www.fotmob.com/matches/chelsea-vs-man-city/2d55kw#4813688
      /matches/chelsea-vs-man-city/2d55kw#4813688
      /match/4317858/overview/man-city-vs-arsenal
      https://www.fotmob.com/match/4317858/overview/...

    Args:
        url:    Match page URL.
        engine: Fetch backend — "requests" (default), "scrapling", or "auto".

    Returns the same dict shape as scraper.parse_match().
    Raises requests.HTTPError on network failures.
    """
    session = make_session()
    raw = fetch_match_json(session, url, engine=engine)
    return _parse_match(raw)
