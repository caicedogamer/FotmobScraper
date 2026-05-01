"""
fetch_backend.py
----------------
Thin abstraction over HTTP fetch backends.

Supported engines:
  "requests"  — default; uses the stdlib-backed requests library.
  "scrapling" — optional; uses Scrapling's Fetcher (HTTP, no browser).
  "auto"      — try requests first; if blocked (4xx/5xx or suspicious
                HTML), fall back to Scrapling automatically.

Scrapling is an optional dependency. If it is not installed:
  - engine="requests"  works normally.
  - engine="scrapling" raises ImportError with install instructions.
  - engine="auto"      works as requests-only (no fallback).

Install Scrapling:
    pip install "scrapling[fetchers]"
    scrapling install          # downloads browser fingerprinting assets
"""

import json as _json
import logging

logger = logging.getLogger(__name__)

VALID_ENGINES: frozenset = frozenset({"requests", "scrapling", "auto"})

try:
    import scrapling as _scrapling_mod          # noqa: F401 — presence check only
    _SCRAPLING_AVAILABLE = True
except ImportError:
    _SCRAPLING_AVAILABLE = False


# ── Public helpers ────────────────────────────────────────────────────────────

def scrapling_available() -> bool:
    """Return True if Scrapling is installed and importable."""
    return _SCRAPLING_AVAILABLE


def require_scrapling() -> None:
    """Raise ImportError with install instructions if Scrapling is missing."""
    if not _SCRAPLING_AVAILABLE:
        raise ImportError(
            "Scrapling is not installed.\n"
            "Install it with:  pip install 'scrapling[fetchers]'\n"
            "Some modes also need:  scrapling install"
        )


# ── Block detection ───────────────────────────────────────────────────────────

def _is_likely_blocked(text: str) -> bool:
    """Heuristic: does this HTML look like a Cloudflare / bot-challenge page?"""
    if not text or len(text) < 300:
        return True
    t = text.lower()
    if "just a moment" in t:
        return True
    if "cloudflare" in t and "security" in t:
        return True
    if "enable javascript" in t and ("challenge" in t or "protection" in t):
        return True
    return False


def _is_block_status(exc: Exception) -> bool:
    """Return True if the exception carries a block-style HTTP status code."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (403, 429, 503)


# ── Backend implementations ───────────────────────────────────────────────────

def _fetch_text_requests(url, headers=None, timeout=15, session=None) -> str:
    if session is not None:
        resp = session.get(url, headers=headers, timeout=timeout)
    else:
        import requests as _requests
        resp = _requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _fetch_text_scrapling(url, headers=None, timeout=15) -> str:
    require_scrapling()
    from scrapling.fetchers import Fetcher
    fetcher = Fetcher(auto_match=False)
    resp = fetcher.get(url, headers=headers or {}, timeout=timeout, stealthy_headers=True)
    if resp is None:
        raise RuntimeError(f"Scrapling returned None for {url!r}")
    html = getattr(resp, "html_content", None)
    if html and isinstance(html, str):
        return html
    raise RuntimeError(f"Scrapling response for {url!r} has no usable .html_content")


# ── Public fetch API ──────────────────────────────────────────────────────────

def fetch_text(url, headers=None, timeout=15, engine="requests", session=None) -> str:
    """
    Fetch a URL and return the response body as text.

    Args:
        url:     Target URL.
        headers: Extra headers to send (merged with defaults for requests).
        timeout: Request timeout in seconds.
        engine:  "requests" | "scrapling" | "auto"
        session: requests.Session to reuse (requests / auto engine only).

    Returns:
        Response body as a str.

    Raises:
        ImportError  — engine="scrapling" and Scrapling not installed.
        ValueError   — unknown engine name.
        RuntimeError — Scrapling returned an unusable response.
        requests.HTTPError / requests.RequestException — network errors.
    """
    if engine == "requests":
        return _fetch_text_requests(url, headers=headers, timeout=timeout, session=session)

    if engine == "scrapling":
        return _fetch_text_scrapling(url, headers=headers, timeout=timeout)

    if engine == "auto":
        try:
            text = _fetch_text_requests(url, headers=headers, timeout=timeout, session=session)
            if not _is_likely_blocked(text):
                return text
            logger.info("auto: response looks blocked for %s — falling back to Scrapling", url)
        except Exception as exc:
            if not _is_block_status(exc):
                raise
            logger.info(
                "auto: HTTP %s for %s — falling back to Scrapling",
                getattr(getattr(exc, "response", None), "status_code", "?"),
                url,
            )
        if not _SCRAPLING_AVAILABLE:
            raise ImportError(
                "auto mode: requests was blocked and Scrapling is not installed.\n"
                "Install with:  pip install 'scrapling[fetchers]'"
            )
        return _fetch_text_scrapling(url, headers=headers, timeout=timeout)

    raise ValueError(f"Unknown engine: {engine!r}. Valid engines: {sorted(VALID_ENGINES)}")


def fetch_json(url, headers=None, timeout=15, engine="requests", session=None) -> dict:
    """Fetch a URL and parse the response as JSON."""
    return _json.loads(
        fetch_text(url, headers=headers, timeout=timeout, engine=engine, session=session)
    )
