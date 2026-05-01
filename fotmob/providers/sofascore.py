"""
providers/sofascore.py
----------------------
Sofascore match provider — NOT YET IMPLEMENTED.

Implementation guide
--------------------
When ready to implement:

1.  Identify API endpoints.
    Match summary:  GET https://api.sofascore.com/api/v1/event/{match_id}
    Lineups:        GET https://api.sofascore.com/api/v1/event/{match_id}/lineups
    Incidents:      GET https://api.sofascore.com/api/v1/event/{match_id}/incidents

2.  Parse the match_id from the URL.
    Sofascore URLs look like:
      https://www.sofascore.com/manchester-city-arsenal/GhsJb#12345678
    The fragment (#12345678) is the match_id.

3.  Normalise the response to the common match dict shape:
    {
        match_id, date, league, venue,
        home_team, away_team, home_id, away_id,
        score, home_formation, away_formation,
        home_lineup: [{
            id, name, shirt, starter, rating,
            x_norm, y_norm,           # Sofascore provides position as averageX/averageY
            goals, assists, yellow, red, motm,
            subbed_on, subbed_off,
        }],
        away_lineup: [...],
        events: [{type, minute, player, team, detail}],
    }

4.  Handle rate-limiting.
    Sofascore returns 429 if you hit the API too fast.
    Add time.sleep() between requests and honour Retry-After headers.

5.  Register the provider:
    In providers/__init__.py add "sofascore" to _ENABLED.

6.  Add "sofascore" to the UI provider selector in app.py (remove the
    disabled attribute from the <option> element).
"""


def fetch_match(url: str, engine: str = "requests") -> dict:
    raise NotImplementedError(
        "Sofascore provider is not yet implemented. "
        "See providers/sofascore.py for the implementation guide."
    )
