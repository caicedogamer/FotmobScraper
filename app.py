"""
app.py
------
Flask web app that fetches FotMob player data and displays it in tables.

Usage:
    python app.py
    Then open http://localhost:5000
"""

import json
import logging
import os
import queue
import sys
import threading
from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context
from fotmob.scraper import make_session, fetch_player_json, parse_player, search_players
from fotmob.db import (
    init_db, upsert_player, load_player, list_players,
    upsert_imported_match, list_imported_matches,
)
from bulk import bulk_scrape
from bulk_matches import bulk_import_matches, MatchImportResult
from fotmob.providers import PROVIDERS, is_enabled
from fotmob.fetch_backend import VALID_ENGINES, scrapling_available
from fotmob.predictor import get_predictions, LEAGUES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
logger = logging.getLogger(__name__)
init_db()

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FotMob Player Stats</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0e1117;
      color: #e4e6eb;
      min-height: 100vh;
      display: flex;
    }

    /* Sidebar */
    .sidebar {
      width: 220px;
      min-width: 220px;
      background: #13151f;
      border-right: 1px solid #2e3340;
      padding: 1.5rem 0;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
    }
    .sidebar h2 {
      font-size: .72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #4b5563;
      padding: 0 1rem .6rem;
      border-bottom: 1px solid #2e3340;
      margin-bottom: .5rem;
    }
    .sidebar a {
      display: block;
      padding: .5rem 1rem;
      font-size: .85rem;
      color: #9ca3af;
      text-decoration: none;
      border-left: 3px solid transparent;
      transition: all .12s;
    }
    .sidebar a:hover { background: #1a1d27; color: #e4e6eb; }
    .sidebar a.active { border-left-color: #3b82f6; color: #93c5fd; background: #1a1d27; }
    .sidebar .sub { font-size: .75rem; color: #4b5563; padding: .15rem 1rem; }

    /* Main content */
    .main {
      flex: 1;
      padding: 2rem 1.5rem;
      overflow-x: auto;
    }

    h1 {
      text-align: center;
      font-size: 1.6rem;
      font-weight: 700;
      color: #fff;
      margin-bottom: 1.8rem;
      letter-spacing: 0.03em;
    }

    /* Search form */
    /* Search */
    .search-wrap {
      position: relative;
      width: 360px;
      margin: 0 auto 2rem;
    }
    .search-wrap input {
      width: 100%;
      padding: .65rem 1rem;
      border: 1px solid #2e3340;
      border-radius: 10px;
      background: #1a1d27;
      color: #e4e6eb;
      font-size: 1rem;
    }
    .search-wrap input::placeholder { color: #555; }
    .search-wrap input:focus { outline: 2px solid #3b82f6; border-color: transparent; }
    .search-results {
      position: absolute;
      top: calc(100% + 4px);
      left: 0; right: 0;
      background: #1a1d27;
      border: 1px solid #2e3340;
      border-radius: 10px;
      overflow: hidden;
      z-index: 100;
      display: none;
    }
    .search-results.open { display: block; }
    .search-result-item {
      padding: .6rem 1rem;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: .9rem;
    }
    .search-result-item:hover { background: #252836; }
    .search-result-item + .search-result-item { border-top: 1px solid #1e2130; }
    .result-team { font-size: .75rem; color: #6b7280; }

    /* Error */
    .error {
      max-width: 560px;
      margin: 0 auto 1.5rem;
      background: #2d1b1b;
      border: 1px solid #7f1d1d;
      color: #fca5a5;
      padding: .8rem 1rem;
      border-radius: 8px;
      font-size: .9rem;
    }

    /* Player header card */
    .player-header {
      max-width: 760px;
      margin: 0 auto 1.8rem;
      background: #1a1d27;
      border: 1px solid #2e3340;
      border-radius: 12px;
      padding: 1.4rem 1.8rem;
      display: flex;
      gap: 1.6rem;
      align-items: center;
    }
    .player-photo {
      flex-shrink: 0;
      width: 96px;
      height: 96px;
      border-radius: 50%;
      object-fit: cover;
      background: #0e1117;
      border: 2px solid #2e3340;
    }
    .player-photo-fallback {
      flex-shrink: 0;
      width: 96px;
      height: 96px;
      border-radius: 50%;
      background: #1e2130;
      border: 2px solid #2e3340;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 2.2rem;
      color: #4b5563;
    }
    .player-details {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: .7rem 1.5rem;
      flex: 1;
    }
    .player-details h2 {
      grid-column: 1 / -1;
      font-size: 1.35rem;
      font-weight: 700;
      color: #fff;
      margin-bottom: .1rem;
    }
    .info-item { display: flex; flex-direction: column; gap: .15rem; }
    .info-label { font-size: .72rem; text-transform: uppercase; letter-spacing: .07em; color: #6b7280; }
    .info-value { font-size: .95rem; font-weight: 500; color: #e4e6eb; }

    /* Tables */
    .tables-grid {
      max-width: 900px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.5rem;
    }
    @media (max-width: 640px) { .tables-grid { grid-template-columns: 1fr; } }
    .career-wrap, .matches-wrap { grid-column: 1 / -1; }

    .card {
      background: #1a1d27;
      border: 1px solid #2e3340;
      border-radius: 12px;
      overflow: hidden;
    }
    .card-title {
      padding: .75rem 1.1rem;
      font-size: .8rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #9ca3af;
      border-bottom: 1px solid #2e3340;
      background: #161822;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: .88rem;
    }
    th {
      padding: .5rem .9rem;
      text-align: left;
      font-size: .72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .07em;
      color: #6b7280;
      border-bottom: 1px solid #2e3340;
      background: #161822;
    }
    th.num, td.num { text-align: right; }
    td {
      padding: .5rem .9rem;
      border-bottom: 1px solid #1e2130;
      color: #d1d5db;
    }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #1e2130; }
    .badge {
      display: inline-block;
      padding: .15rem .5rem;
      border-radius: 999px;
      font-size: .78rem;
      font-weight: 600;
      background: #1e3a5f;
      color: #93c5fd;
    }
    .active-badge { background: #14532d; color: #86efac; }
    .result-W { display:inline-block; padding:.1rem .45rem; border-radius:4px; font-weight:700; font-size:.78rem; background:#14532d; color:#86efac; }
    .result-L { display:inline-block; padding:.1rem .45rem; border-radius:4px; font-weight:700; font-size:.78rem; background:#450a0a; color:#fca5a5; }
    .result-D { display:inline-block; padding:.1rem .45rem; border-radius:4px; font-weight:700; font-size:.78rem; background:#1c1917; color:#a8a29e; }
    .motm { color:#fbbf24; font-size:.8rem; margin-left:.3rem; }
    .rating-high { color:#86efac; font-weight:600; }
    .rating-low  { color:#fca5a5; font-weight:600; }
  </style>
</head>
<body>
<div class="body-wrap" style="display:flex;width:100%;">

  <!-- Sidebar: saved players -->
  <nav class="sidebar">
    <h2>Saved Players</h2>
    <a href="/predictions" style="color:#a78bfa;margin-bottom:.1rem;">⚡ Predictions</a>
    <a href="/matches/imported" style="color:#fb923c;margin-bottom:.1rem;">📋 Matches</a>
    <a href="/bulk" style="color:#3b82f6;margin-bottom:.1rem;">+ Bulk Players</a>
    <a href="/matches/bulk" style="color:#34d399;margin-bottom:.5rem;">+ Bulk Matches</a>
    {% if saved_players %}
      {% for p in saved_players %}
      <a href="/?player_id={{ p.id }}&slug={{ p.slug }}"
         class="{{ 'active' if request.args.get('player_id')|string == p.id|string else '' }}">
        {{ p.name or p.slug }}
      </a>
      <span class="sub">{{ p.club or '—' }}</span>
      {% endfor %}
    {% else %}
      <span class="sub">No players saved yet.</span>
    {% endif %}
  </nav>

  <div class="main">
  <h1>FotMob Player Stats</h1>

  <div class="search-wrap">
    <input id="search-input" type="text" placeholder="Search player name..."
           autocomplete="off" value="{{ player.name if player else '' }}">
    <div id="search-results" class="search-results"></div>
  </div>

  {% if cache_path %}
  <p style="text-align:center;font-size:.78rem;color:#4b5563;margin-bottom:1rem;">
    {% if from_cache %}Loaded from cache{% else %}Saved to DB{% endif %}
    {% if from_cache %}
      &nbsp;&mdash;&nbsp;<a href="?player_id={{ request.args.get('player_id') }}&slug={{ request.args.get('slug') }}&refresh=1"
               style="color:#3b82f6;text-decoration:none;">Refresh</a>
    {% endif %}
  </p>
  {% endif %}

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  {% if player %}
  <div class="player-header">
    {% if player.image_url %}
    <img class="player-photo"
         src="{{ player.image_url }}"
         alt="{{ player.name }}"
         onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
    <div class="player-photo-fallback" style="display:none">&#9917;</div>
    {% else %}
    <div class="player-photo-fallback">&#9917;</div>
    {% endif %}
    <div class="player-details">
      <h2>{{ player.name }}</h2>
      <div class="info-item">
        <span class="info-label">Club</span>
        <span class="info-value">{{ player.club or '—' }}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Position</span>
        <span class="info-value">{{ player.position or '—' }}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Nationality</span>
        <span class="info-value">{{ player.nationality or '—' }}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Age</span>
        <span class="info-value">{{ player.age or '—' }}</span>
      </div>
      <div class="info-item">
        <span class="info-label">Jersey</span>
        <span class="info-value">{% if player.jersey_number %}#{{ player.jersey_number }}{% else %}—{% endif %}</span>
      </div>
    </div>
  </div>

  <div class="tables-grid">

    {% if player.season_stats %}
    <div class="card">
      <div class="card-title">Season Stats (current league)</div>
      <table>
        <thead>
          <tr><th>Stat</th><th class="num">Value</th></tr>
        </thead>
        <tbody>
          {% for label, value in player.season_stats.items() %}
          <tr>
            <td>{{ label }}</td>
            <td class="num"><span class="badge">{{ value }}</span></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% if player.career %}
    <div class="card career-wrap">
      <div class="card-title">Career — {{ player.career | length }} clubs</div>
      <table>
        <thead>
          <tr>
            <th>Club</th>
            <th>Start</th>
            <th>End</th>
            <th class="num">Apps</th>
            <th class="num">G</th>
            <th class="num">A</th>
          </tr>
        </thead>
        <tbody>
          {% for s in player.career %}
          <tr>
            <td>{{ s.team or '—' }}</td>
            <td>{{ s.start or '—' }}</td>
            <td>{% if s.end == 'present' %}<span class="badge active-badge">present</span>{% else %}{{ s.end or '—' }}{% endif %}</td>
            <td class="num">{{ s.appearances or '—' }}</td>
            <td class="num">{{ s.goals or '—' }}</td>
            <td class="num">{{ s.assists or '—' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% if player.matches %}
    <div class="card matches-wrap">
      <div class="card-title">Recent Matches — {{ player.matches | length }}</div>
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Fixture</th>
            <th>League</th>
            <th class="num">Score</th>
            <th class="num">Res</th>
            <th class="num">Mins</th>
            <th class="num">G</th>
            <th class="num">A</th>
            <th class="num">Rating</th>
          </tr>
        </thead>
        <tbody>
          {% for m in player.matches %}
          <tr>
            <td>{{ m.date }}</td>
            <td>
              {% if m.url %}
              <a href="https://www.fotmob.com{{ m.url }}" target="_blank"
                 style="color:#93c5fd;text-decoration:none;">{{ m.fixture }}</a>
              {% else %}{{ m.fixture }}{% endif %}
              {% if m.motm %}<span class="motm" title="Man of the Match">★</span>{% endif %}
            </td>
            <td style="color:#6b7280;font-size:.82rem;">{{ m.league or '—' }}</td>
            <td class="num">{{ m.score }}</td>
            <td class="num"><span class="result-{{ m.result }}">{{ m.result }}</span></td>
            <td class="num">{{ m.mins or '—' }}</td>
            <td class="num">{{ m.goals if m.goals else '—' }}</td>
            <td class="num">{{ m.assists if m.assists else '—' }}</td>
            <td class="num">
              {% if m.rating %}
              <span class="{{ 'rating-high' if m.rating | float >= 7.5 else 'rating-low' if m.rating | float < 6.5 else '' }}">{{ m.rating }}</span>
              {% else %}—{% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

  </div>
  {% endif %}

</div><!-- .main -->
</div><!-- .body-wrap -->

<script>
(function () {
  const input   = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  let debounce;

  input.addEventListener('input', () => {
    clearTimeout(debounce);
    const q = input.value.trim();
    if (q.length < 2) { results.classList.remove('open'); results.innerHTML = ''; return; }
    debounce = setTimeout(() => fetchResults(q), 300);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') { results.classList.remove('open'); results.innerHTML = ''; }
  });

  document.addEventListener('click', e => {
    if (!input.contains(e.target) && !results.contains(e.target)) {
      results.classList.remove('open');
    }
  });

  async function fetchResults(q) {
    const resp = await fetch('/search?q=' + encodeURIComponent(q));
    const data = await resp.json();
    results.innerHTML = '';
    if (!data.length) { results.classList.remove('open'); return; }
    for (const p of data) {
      const item = document.createElement('div');
      item.className = 'search-result-item';
      item.dataset.id = p.id;
      item.dataset.slug = p.slug;
      const nameSpan = document.createElement('span');
      nameSpan.textContent = p.name;
      const teamSpan = document.createElement('span');
      teamSpan.className = 'result-team';
      teamSpan.textContent = p.team;
      item.appendChild(nameSpan);
      item.appendChild(teamSpan);
      item.addEventListener('click', () => {
        window.location = '/?player_id=' + encodeURIComponent(p.id) + '&slug=' + encodeURIComponent(p.slug);
      });
      results.appendChild(item);
    }
    results.classList.add('open');
  }
})();
</script>

</body>
</html>
"""


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        return jsonify(search_players(q))
    except Exception:
        logger.exception("Search failed for query %r", q)
        return jsonify([])


@app.route("/")
def index():
    player_id = request.args.get("player_id", "").strip()
    slug = request.args.get("slug", "").strip()
    force_refresh = bool(request.args.get("refresh"))
    player = None
    error = None
    cache_path = None
    from_cache = False

    if player_id and slug:
        try:
            pid = int(player_id)
        except ValueError:
            error = "Invalid player ID."
            pid = None

        if pid is not None:
            if not force_refresh:
                cached = load_player(pid)
                if cached:
                    player = cached
                    from_cache = True
                    cache_path = "fotmob.db"

            if player is None:
                try:
                    session = make_session()
                    raw = fetch_player_json(session, pid, slug)
                    player = parse_player(raw)
                    upsert_player(player)
                    cache_path = "fotmob.db"
                except Exception as exc:
                    logger.exception("Error fetching player %s/%s", pid, slug)
                    error = str(exc)

    return render_template_string(
        TEMPLATE, player=player, error=error, request=request,
        cache_path=cache_path, from_cache=from_cache,
        saved_players=list_players(),
    )


BULK_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bulk Import — FotMob</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0e1117; color: #e4e6eb;
      min-height: 100vh; display: flex;
    }
    .sidebar {
      width: 220px; min-width: 220px; background: #13151f;
      border-right: 1px solid #2e3340; padding: 1.5rem 0;
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
    }
    .sidebar h2 {
      font-size: .72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .08em; color: #4b5563;
      padding: 0 1rem .6rem; border-bottom: 1px solid #2e3340; margin-bottom: .5rem;
    }
    .sidebar a {
      display: block; padding: .5rem 1rem; font-size: .85rem;
      color: #9ca3af; text-decoration: none;
      border-left: 3px solid transparent; transition: all .12s;
    }
    .sidebar a:hover { background: #1a1d27; color: #e4e6eb; }
    .sidebar a.active { border-left-color: #3b82f6; color: #93c5fd; background: #1a1d27; }
    .sidebar .sub { font-size: .75rem; color: #4b5563; padding: .15rem 1rem; }
    .main { flex: 1; padding: 2rem 2rem; max-width: 760px; }
    h1 { font-size: 1.4rem; font-weight: 700; color: #fff; margin-bottom: .4rem; }
    .subtitle { font-size: .85rem; color: #6b7280; margin-bottom: 1.8rem; }
    textarea {
      width: 100%; height: 180px;
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 10px;
      color: #e4e6eb; font-size: .9rem; padding: .8rem 1rem;
      resize: vertical; font-family: monospace;
    }
    textarea:focus { outline: 2px solid #3b82f6; border-color: transparent; }
    .controls {
      display: flex; gap: .8rem; align-items: center;
      margin-top: .8rem; flex-wrap: wrap;
    }
    .controls label { font-size: .8rem; color: #6b7280; display: flex; align-items: center; gap: .4rem; }
    .controls input[type="number"] {
      width: 60px; padding: .35rem .5rem;
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 6px;
      color: #e4e6eb; font-size: .85rem;
    }
    button[type="submit"] {
      padding: .55rem 1.4rem; background: #3b82f6; color: #fff;
      border: none; border-radius: 8px; font-size: .95rem;
      font-weight: 600; cursor: pointer; transition: background .15s;
    }
    button[type="submit"]:hover { background: #2563eb; }
    button[type="submit"]:disabled { background: #1e3a5f; color: #6b7280; cursor: not-allowed; }

    /* Progress */
    #progress { margin-top: 1.5rem; display: none; }
    .progress-bar-wrap {
      height: 6px; background: #1e2130; border-radius: 999px; margin-bottom: 1rem;
    }
    .progress-bar {
      height: 6px; background: #3b82f6; border-radius: 999px;
      width: 0%; transition: width .3s;
    }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; margin-top: .5rem; }
    th {
      padding: .45rem .8rem; text-align: left; font-size: .72rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: .07em; color: #6b7280;
      border-bottom: 1px solid #2e3340; background: #161822;
    }
    td { padding: .45rem .8rem; border-bottom: 1px solid #1e2130; color: #d1d5db; }
    tr:last-child td { border-bottom: none; }
    .badge-ok   { color: #86efac; font-weight: 700; }
    .badge-err  { color: #fca5a5; font-weight: 700; }
    .badge-miss { color: #fbbf24; font-weight: 700; }
    #summary { margin-top: 1rem; font-size: .85rem; color: #6b7280; }
  </style>
</head>
<body>
<div style="display:flex;width:100%;">
  <nav class="sidebar">
    <h2>Saved Players</h2>
    <a href="/predictions" style="color:#a78bfa;">⚡ Predictions</a>
    <a href="/matches/imported" style="color:#fb923c;">📋 Matches</a>
    <a href="/matches/bulk" style="color:#34d399;">+ Bulk Matches</a>
    {% for p in saved_players %}
    <a href="/?player_id={{ p.id }}&slug={{ p.slug }}">{{ p.name or p.slug }}</a>
    <span class="sub">{{ p.club or '—' }}</span>
    {% else %}
    <span class="sub">No players yet.</span>
    {% endfor %}
  </nav>

  <div class="main">
    <h1>Bulk Import</h1>
    <p class="subtitle">One player name per line. The top search result is used for each.</p>

    <form id="bulk-form">
      <textarea id="names" placeholder="Erling Haaland&#10;Kylian Mbappe&#10;Bruno Fernandes"></textarea>
      <div class="controls">
        <label>Workers
          <input type="number" id="workers" value="3" min="1" max="3">
        </label>
        <label>Delay (s)
          <input type="number" id="delay" value="1.0" min="0.5" max="5" step="0.5">
        </label>
        <label>Engine
          <select id="engine">
            <option value="requests">requests (default)</option>
            <option value="auto">auto (Scrapling fallback)</option>
            <option value="scrapling">scrapling</option>
          </select>
        </label>
        <button type="submit">Import</button>
        <span id="status-text" style="font-size:.82rem;color:#6b7280;"></span>
      </div>
    </form>

    <div id="progress">
      <div class="progress-bar-wrap"><div class="progress-bar" id="bar"></div></div>
      <table>
        <thead>
          <tr>
            <th>Player</th><th>Club</th><th>Status</th><th style="text-align:right">Matches</th>
          </tr>
        </thead>
        <tbody id="results-body"></tbody>
      </table>
      <div id="summary"></div>
    </div>
  </div>
</div>

<script>
document.getElementById('bulk-form').addEventListener('submit', async e => {
  e.preventDefault();
  const names = document.getElementById('names').value
    .split('\\n').map(s => s.trim()).filter(Boolean);
  if (!names.length) return;

  const workers = document.getElementById('workers').value;
  const delay   = document.getElementById('delay').value;
  const engine  = document.getElementById('engine').value;
  const btn     = document.querySelector('button[type="submit"]');
  const statusEl = document.getElementById('status-text');
  const progress = document.getElementById('progress');
  const bar      = document.getElementById('bar');
  const tbody    = document.getElementById('results-body');
  const summary  = document.getElementById('summary');

  btn.disabled = true;
  progress.style.display = 'block';
  tbody.innerHTML = '';
  summary.textContent = '';
  statusEl.textContent = `Importing ${names.length} player(s)...`;

  let done = 0, ok = 0, failed = 0, totalMatches = 0;

  const resp = await fetch('/bulk/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({names, workers: +workers, delay: +delay, engine}),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const {value, done: streamDone} = await reader.read();
    if (streamDone) break;
    buf += decoder.decode(value, {stream: true});
    const lines = buf.split('\\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      const ev = JSON.parse(line.slice(5).trim());
      done++;
      bar.style.width = Math.round(done / names.length * 100) + '%';

      if (ev.status === 'ok') { ok++; totalMatches += ev.matches; }
      else failed++;

      const tr = document.createElement('tr');
      if (ev.status === 'ok') {
        const tdName = document.createElement('td');
        tdName.textContent = ev.name;
        const tdClub = document.createElement('td');
        tdClub.textContent = ev.club || '—';
        const tdStatus = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = 'badge-ok';
        badge.textContent = '✓ saved';
        tdStatus.appendChild(badge);
        const tdMatches = document.createElement('td');
        tdMatches.style.textAlign = 'right';
        tdMatches.textContent = ev.matches;
        tr.append(tdName, tdClub, tdStatus, tdMatches);
      } else {
        const tdName = document.createElement('td');
        tdName.textContent = ev.query;
        const tdClub = document.createElement('td');
        tdClub.textContent = '—';
        const tdStatus = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = ev.status === 'not_found' ? 'badge-miss' : 'badge-err';
        badge.textContent = ev.status === 'not_found' ? '? not found' : '✗ error';
        tdStatus.appendChild(badge);
        if (ev.error) {
          const errSpan = document.createElement('span');
          errSpan.style.cssText = 'font-size:.75rem;color:#4b5563;margin-left:.4rem';
          errSpan.textContent = ev.error;
          tdStatus.appendChild(errSpan);
        }
        tr.append(tdName, tdClub, tdStatus, document.createElement('td'));
      }
      tbody.prepend(tr);
    }
  }

  statusEl.textContent = '';
  summary.textContent = `Done — ${ok} saved, ${failed} failed, ${totalMatches} total matches stored.`;
  btn.disabled = false;
});
</script>
</body>
</html>
"""


@app.route("/bulk")
def bulk_page():
    return render_template_string(BULK_TEMPLATE, saved_players=list_players())


@app.route("/bulk/stream", methods=["POST"])
def bulk_stream():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    names_raw = body.get("names")
    if not isinstance(names_raw, list):
        return jsonify({"error": "'names' must be a list"}), 400
    names = [n.strip() for n in names_raw if isinstance(n, str) and str(n).strip()]
    if not names:
        return jsonify({"error": "No valid names provided"}), 400

    try:
        workers = max(1, min(int(body.get("workers", 3)), 3))
    except (TypeError, ValueError):
        return jsonify({"error": "'workers' must be an integer"}), 400

    try:
        delay = max(0.5, min(float(body.get("delay", 1.0)), 5.0))
    except (TypeError, ValueError):
        return jsonify({"error": "'delay' must be a number"}), 400

    engine = str(body.get("engine", "requests")).strip()
    if engine not in VALID_ENGINES:
        return jsonify({"error": f"Invalid engine: {engine!r}. Valid: {sorted(VALID_ENGINES)}"}), 400

    result_queue = queue.Queue()

    def on_result(r):
        if r.status == "ok":
            result_queue.put({
                "status":  "ok",
                "name":    r.player["name"],
                "club":    r.player.get("club", ""),
                "matches": r.matches,
            })
        else:
            result_queue.put({
                "status": r.status,
                "query":  r.name,
                "error":  r.error,
            })

    def run():
        try:
            bulk_scrape(names, workers=workers, delay=delay, progress_cb=on_result,
                        engine=engine)
        except Exception:
            logger.exception("bulk_scrape raised unexpectedly")
        finally:
            result_queue.put(None)  # sentinel always sent

    threading.Thread(target=run, daemon=True).start()

    def generate():
        while True:
            item = result_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


PREDICTIONS_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Match Predictor — FotMob</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0e1117; color: #e4e6eb; min-height: 100vh; display: flex;
    }
    .sidebar {
      width: 220px; min-width: 220px; background: #13151f;
      border-right: 1px solid #2e3340; padding: 1.5rem 0;
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
    }
    .sidebar h2 {
      font-size: .72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .08em; color: #4b5563;
      padding: 0 1rem .6rem; border-bottom: 1px solid #2e3340; margin-bottom: .5rem;
    }
    .sidebar a {
      display: block; padding: .5rem 1rem; font-size: .85rem;
      color: #9ca3af; text-decoration: none;
      border-left: 3px solid transparent; transition: all .12s;
    }
    .sidebar a:hover { background: #1a1d27; color: #e4e6eb; }
    .sidebar a.active { border-left-color: #a78bfa; color: #c4b5fd; background: #1a1d27; }
    .sidebar .sub { font-size: .75rem; color: #4b5563; padding: .15rem 1rem; }
    .main { flex: 1; padding: 2rem 1.5rem; max-width: 900px; }
    h1 { font-size: 1.5rem; font-weight: 700; color: #fff; margin-bottom: .4rem; }
    .subtitle { font-size: .85rem; color: #6b7280; margin-bottom: 1.5rem; }

    /* League tabs */
    .league-tabs { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1.8rem; }
    .league-tab {
      padding: .45rem 1rem; border-radius: 8px; font-size: .85rem; font-weight: 600;
      text-decoration: none; color: #9ca3af; background: #1a1d27; border: 1px solid #2e3340;
      transition: all .15s; cursor: pointer;
    }
    .league-tab:hover { background: #252836; color: #e4e6eb; border-color: #3b4260; }
    .league-tab.active { background: #2d1f50; color: #c4b5fd; border-color: #7c3aed; }

    /* Match cards */
    .match-grid { display: grid; gap: 1rem; }
    .match-card {
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 12px; padding: 1.2rem 1.4rem;
    }
    .match-header {
      display: flex; justify-content: space-between; align-items: center; margin-bottom: .8rem;
    }
    .match-date { font-size: .75rem; color: #6b7280; }
    .match-time { font-size: .75rem; color: #4b5563; }
    .match-teams {
      display: flex; align-items: center; gap: 1rem; margin-bottom: .9rem;
    }
    .team-name { font-size: 1rem; font-weight: 600; color: #e4e6eb; flex: 1; }
    .team-name.away { text-align: right; }
    .score-badge {
      background: #0e1117; border: 1px solid #2e3340; border-radius: 8px;
      padding: .3rem .9rem; font-size: 1.2rem; font-weight: 700; color: #fff;
      white-space: nowrap;
    }
    .outcome-badge {
      display: inline-block; padding: .2rem .65rem; border-radius: 999px;
      font-size: .78rem; font-weight: 700; margin-bottom: .8rem;
    }
    .outcome-home { background: #14532d; color: #86efac; }
    .outcome-draw { background: #1c1917; color: #a8a29e; }
    .outcome-away { background: #1e3a5f; color: #93c5fd; }

    /* Probability bars */
    .prob-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: .5rem; margin-top: .6rem; }
    .prob-item { display: flex; flex-direction: column; gap: .25rem; }
    .prob-label { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em; color: #6b7280; }
    .prob-bar-wrap { height: 5px; background: #1e2130; border-radius: 999px; }
    .prob-bar { height: 5px; border-radius: 999px; }
    .bar-home { background: #22c55e; }
    .bar-draw { background: #a8a29e; }
    .bar-away { background: #3b82f6; }
    .prob-pct { font-size: .82rem; font-weight: 600; color: #e4e6eb; }

    /* xG row */
    .xg-row {
      display: flex; justify-content: space-between; margin-top: .6rem;
      font-size: .78rem; color: #6b7280;
    }
    .xg-val { color: #9ca3af; font-weight: 500; }

    /* Error / empty */
    .alert {
      background: #1c1917; border: 1px solid #3f3f46; border-radius: 10px;
      padding: 1rem 1.2rem; color: #a8a29e; font-size: .9rem;
    }
    .loading-msg { color: #6b7280; font-size: .9rem; margin-top: 1rem; }
  </style>
</head>
<body>
<div style="display:flex;width:100%;">
  <nav class="sidebar">
    <h2>Navigation</h2>
    <a href="/">&#9917; Player Stats</a>
    <a href="/predictions" class="active" style="color:#c4b5fd;">⚡ Predictions</a>
    <a href="/matches/imported" style="color:#fb923c;">📋 Matches</a>
    <a href="/bulk" style="color:#3b82f6;">+ Bulk Players</a>
    <a href="/matches/bulk" style="color:#34d399;">+ Bulk Matches</a>
    {% if saved_players %}
    <h2 style="margin-top:1rem;">Saved Players</h2>
    {% for p in saved_players %}
    <a href="/?player_id={{ p.id }}&slug={{ p.slug }}">{{ p.name or p.slug }}</a>
    <span class="sub">{{ p.club or '—' }}</span>
    {% endfor %}
    {% endif %}
  </nav>

  <div class="main">
    <h1>⚡ Match Predictor</h1>
    <p class="subtitle">
      ML model when trained; Poisson baseline otherwise.
    </p>

    <!-- League selector -->
    <div class="league-tabs">
      {% for key, info in leagues.items() %}
      <a href="/predictions?league={{ key }}&model={{ selected_model }}"
         class="league-tab {{ 'active' if key == selected_league else '' }}">
        {{ info.flag }} {{ info.name }}
      </a>
      {% endfor %}
    </div>

    <div class="league-tabs" style="margin-top:-1rem;">
      {% for key, label in model_modes.items() %}
      <a href="/predictions?league={{ selected_league }}&model={{ key }}"
         class="league-tab {{ 'active' if key == selected_model else '' }}">
        {{ label }}
      </a>
      {% endfor %}
    </div>

    {% if model_label and predictions %}
    <p class="subtitle" style="margin-top:-.8rem;">Model: {{ model_label }}</p>
    {% endif %}

    {% if error and not predictions %}
    <div class="alert">{{ error }}</div>

    {% elif predictions %}
    <div class="match-grid">
      {% for p in predictions %}
      <div class="match-card">
        <div class="match-header">
          <span class="match-date">{{ p.date }}</span>
          <span class="match-time">{% if p.time %}{{ p.time }} UTC{% endif %}</span>
        </div>

        <div class="match-teams">
          <span class="team-name">{{ p.home }}</span>
          <span class="score-badge">{{ p.scoreline }}</span>
          <span class="team-name away">{{ p.away }}</span>
        </div>

        <span class="outcome-badge {{ 'outcome-home' if p.outcome == 'Home Win' else 'outcome-draw' if p.outcome == 'Draw' else 'outcome-away' }}">
          {{ p.outcome }} — {{ p.confidence }}% confidence
        </span>

        <div class="prob-row">
          <div class="prob-item">
            <span class="prob-label">Home Win</span>
            <div class="prob-bar-wrap">
              <div class="prob-bar bar-home" style="width:{{ p.p_home }}%"></div>
            </div>
            <span class="prob-pct">{{ p.p_home }}%</span>
          </div>
          <div class="prob-item">
            <span class="prob-label">Draw</span>
            <div class="prob-bar-wrap">
              <div class="prob-bar bar-draw" style="width:{{ p.p_draw }}%"></div>
            </div>
            <span class="prob-pct">{{ p.p_draw }}%</span>
          </div>
          <div class="prob-item">
            <span class="prob-label">Away Win</span>
            <div class="prob-bar-wrap">
              <div class="prob-bar bar-away" style="width:{{ p.p_away }}%"></div>
            </div>
            <span class="prob-pct">{{ p.p_away }}%</span>
          </div>
        </div>

        <div class="xg-row">
          <span>xG {{ p.home }}: <span class="xg-val">{{ p.xg_home }}</span></span>
          <span>xG {{ p.away }}: <span class="xg-val">{{ p.xg_away }}</span></span>
        </div>
      </div>
      {% endfor %}
    </div>

    {% else %}
    <div class="alert">Select a league above to see predictions.</div>
    {% endif %}
  </div>
</div>
</body>
</html>
"""


@app.route("/predictions")
def predictions_page():
    league_key = request.args.get("league", "").strip()
    model_mode = request.args.get("model", "auto").strip()
    if model_mode not in ("auto", "ml", "poisson"):
        model_mode = "auto"
    predictions = []
    error = None
    selected_league = league_key or ""
    model_label = None

    if league_key and league_key in LEAGUES:
        result = get_predictions(league_key, model=model_mode)
        predictions = result.get("predictions", [])
        error = result.get("error")
        model_type = result.get("model_type")
        meta = result.get("model_meta") or {}
        if model_type:
            model_label = str(model_type).replace("_", " ").title()
            trained_on = meta.get("total_matches") or meta.get("train_matches")
            if trained_on:
                model_label += f" · trained on {trained_on:,} matches"

    return render_template_string(
        PREDICTIONS_TEMPLATE,
        leagues=LEAGUES,
        selected_league=selected_league,
        selected_model=model_mode,
        model_modes={
            "auto": "Auto",
            "ml": "ML",
            "poisson": "Poisson",
        },
        model_label=model_label,
        predictions=predictions,
        error=error,
        saved_players=list_players(),
    )


# ── Imported matches listing ──────────────────────────────────────────────────

MATCHES_IMPORTED_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Imported Matches — FotMob</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0e1117; color: #e4e6eb; min-height: 100vh; display: flex;
    }
    .sidebar {
      width: 220px; min-width: 220px; background: #13151f;
      border-right: 1px solid #2e3340; padding: 1.5rem 0;
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
    }
    .sidebar h2 {
      font-size: .72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .08em; color: #4b5563;
      padding: 0 1rem .6rem; border-bottom: 1px solid #2e3340; margin-bottom: .5rem;
    }
    .sidebar a {
      display: block; padding: .5rem 1rem; font-size: .85rem;
      color: #9ca3af; text-decoration: none;
      border-left: 3px solid transparent; transition: all .12s;
    }
    .sidebar a:hover { background: #1a1d27; color: #e4e6eb; }
    .sidebar a.active { border-left-color: #fb923c; color: #fdba74; background: #1a1d27; }
    .sidebar .sub { font-size: .75rem; color: #4b5563; padding: .15rem 1rem; }
    .main { flex: 1; padding: 2rem 1.5rem; overflow-x: auto; }
    h1 { font-size: 1.4rem; font-weight: 700; color: #fff; margin-bottom: .4rem; }
    .subtitle { font-size: .85rem; color: #6b7280; margin-bottom: 1.5rem; }
    .card {
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 12px;
      overflow: hidden; max-width: 1100px;
    }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; }
    th {
      padding: .5rem .9rem; text-align: left; font-size: .72rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: .07em; color: #6b7280;
      border-bottom: 1px solid #2e3340; background: #161822;
    }
    td { padding: .5rem .9rem; border-bottom: 1px solid #1e2130; color: #d1d5db; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #1e2130; }
    .badge-source {
      display: inline-block; padding: .1rem .5rem; border-radius: 999px;
      font-size: .72rem; font-weight: 600; background: #1e3a5f; color: #93c5fd;
    }
    .score { font-weight: 700; color: #fff; }
    .empty { padding: 2rem; text-align: center; color: #4b5563; font-size: .9rem; }
    .action-link { color: #3b82f6; text-decoration: none; font-size: .8rem; }
    .action-link:hover { text-decoration: underline; }
  </style>
</head>
<body>
<div style="display:flex;width:100%;">
  <nav class="sidebar">
    <h2>Navigation</h2>
    <a href="/">&#9917; Player Stats</a>
    <a href="/predictions" style="color:#a78bfa;">⚡ Predictions</a>
    <a href="/matches/imported" class="active">📋 Matches</a>
    <a href="/bulk" style="color:#3b82f6;">+ Bulk Players</a>
    <a href="/matches/bulk" style="color:#34d399;">+ Bulk Matches</a>
    {% if saved_players %}
    <h2 style="margin-top:1rem;">Saved Players</h2>
    {% for p in saved_players %}
    <a href="/?player_id={{ p.id }}&slug={{ p.slug }}">{{ p.name or p.slug }}</a>
    <span class="sub">{{ p.club or '—' }}</span>
    {% endfor %}
    {% endif %}
  </nav>

  <div class="main">
    <h1>📋 Imported Matches</h1>
    <p class="subtitle">
      {{ matches|length }} match{% if matches|length != 1 %}es{% endif %} stored
      &nbsp;·&nbsp;
      <a href="/matches/bulk" style="color:#34d399;text-decoration:none;">+ Import more</a>
    </p>

    <div class="card">
      {% if matches %}
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>League</th>
            <th>Home</th>
            <th>Score</th>
            <th>Away</th>
            <th>Form.</th>
            <th>Source</th>
            <th>Fetched</th>
          </tr>
        </thead>
        <tbody>
          {% for m in matches %}
          <tr>
            <td>{{ m.match_date or '—' }}</td>
            <td style="color:#6b7280;font-size:.82rem;">{{ m.league or '—' }}</td>
            <td>{{ m.home_team or '—' }}</td>
            <td><span class="score">{{ m.score or '—' }}</span></td>
            <td>{{ m.away_team or '—' }}</td>
            <td style="color:#6b7280;font-size:.78rem;">
              {% if m.home_formation %}{{ m.home_formation }}{% endif %}
              {% if m.home_formation and m.away_formation %} / {% endif %}
              {% if m.away_formation %}{{ m.away_formation }}{% endif %}
              {% if not m.home_formation and not m.away_formation %}—{% endif %}
            </td>
            <td><span class="badge-source">{{ m.source }}</span></td>
            <td style="color:#4b5563;font-size:.78rem;">
              {{ m.fetched_at|string|truncate(16, True, '') if m.fetched_at else '—' }}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="empty">
        No matches imported yet.
        <a class="action-link" href="/matches/bulk">Import some →</a>
      </div>
      {% endif %}
    </div>
  </div>
</div>
</body>
</html>
"""


@app.route("/matches/imported")
def matches_imported():
    matches = list_imported_matches()
    return render_template_string(
        MATCHES_IMPORTED_TEMPLATE,
        matches=matches,
        saved_players=list_players(),
    )


# ── Bulk match import page ────────────────────────────────────────────────────

MATCH_BULK_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bulk Match Import — FotMob</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0e1117; color: #e4e6eb; min-height: 100vh; display: flex;
    }
    .sidebar {
      width: 220px; min-width: 220px; background: #13151f;
      border-right: 1px solid #2e3340; padding: 1.5rem 0;
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
    }
    .sidebar h2 {
      font-size: .72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .08em; color: #4b5563;
      padding: 0 1rem .6rem; border-bottom: 1px solid #2e3340; margin-bottom: .5rem;
    }
    .sidebar a {
      display: block; padding: .5rem 1rem; font-size: .85rem;
      color: #9ca3af; text-decoration: none;
      border-left: 3px solid transparent; transition: all .12s;
    }
    .sidebar a:hover { background: #1a1d27; color: #e4e6eb; }
    .sidebar a.active { border-left-color: #34d399; color: #6ee7b7; background: #1a1d27; }
    .sidebar .sub { font-size: .75rem; color: #4b5563; padding: .15rem 1rem; }
    .main { flex: 1; padding: 2rem 2rem; max-width: 820px; }
    h1 { font-size: 1.4rem; font-weight: 700; color: #fff; margin-bottom: .4rem; }
    .subtitle { font-size: .85rem; color: #6b7280; margin-bottom: 1.8rem; }
    textarea {
      width: 100%; height: 180px;
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 10px;
      color: #e4e6eb; font-size: .82rem; padding: .8rem 1rem;
      resize: vertical; font-family: monospace;
    }
    textarea:focus { outline: 2px solid #34d399; border-color: transparent; }
    .controls {
      display: flex; gap: .8rem; align-items: center;
      margin-top: .8rem; flex-wrap: wrap;
    }
    .controls label { font-size: .8rem; color: #6b7280; display: flex; align-items: center; gap: .4rem; }
    .controls input[type="number"], .controls select {
      padding: .35rem .5rem;
      background: #1a1d27; border: 1px solid #2e3340; border-radius: 6px;
      color: #e4e6eb; font-size: .85rem;
    }
    .controls input[type="number"] { width: 60px; }
    .controls select { min-width: 160px; }
    .controls select option:disabled { color: #4b5563; }
    button[type="submit"] {
      padding: .55rem 1.4rem; background: #059669; color: #fff;
      border: none; border-radius: 8px; font-size: .95rem;
      font-weight: 600; cursor: pointer; transition: background .15s;
    }
    button[type="submit"]:hover { background: #047857; }
    button[type="submit"]:disabled { background: #1e3a2f; color: #4b5563; cursor: not-allowed; }
    #progress { margin-top: 1.5rem; display: none; }
    .progress-bar-wrap { height: 6px; background: #1e2130; border-radius: 999px; margin-bottom: 1rem; }
    .progress-bar { height: 6px; background: #34d399; border-radius: 999px; width: 0%; transition: width .3s; }
    table { width: 100%; border-collapse: collapse; font-size: .85rem; margin-top: .5rem; }
    th {
      padding: .45rem .8rem; text-align: left; font-size: .72rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: .07em; color: #6b7280;
      border-bottom: 1px solid #2e3340; background: #161822;
    }
    td { padding: .45rem .8rem; border-bottom: 1px solid #1e2130; color: #d1d5db; }
    tr:last-child td { border-bottom: none; }
    .badge-ok   { color: #86efac; font-weight: 700; }
    .badge-err  { color: #fca5a5; font-weight: 700; }
    .badge-ns   { color: #fbbf24; font-weight: 700; }
    #summary { margin-top: 1rem; font-size: .85rem; color: #6b7280; }
  </style>
</head>
<body>
<div style="display:flex;width:100%;">
  <nav class="sidebar">
    <h2>Navigation</h2>
    <a href="/">&#9917; Player Stats</a>
    <a href="/predictions" style="color:#a78bfa;">⚡ Predictions</a>
    <a href="/matches/imported" style="color:#fb923c;">📋 Matches</a>
    <a href="/bulk" style="color:#3b82f6;">+ Bulk Players</a>
    <a href="/matches/bulk" class="active">+ Bulk Matches</a>
    {% if saved_players %}
    <h2 style="margin-top:1rem;">Saved Players</h2>
    {% for p in saved_players %}
    <a href="/?player_id={{ p.id }}&slug={{ p.slug }}">{{ p.name or p.slug }}</a>
    <span class="sub">{{ p.club or '—' }}</span>
    {% endfor %}
    {% endif %}
  </nav>

  <div class="main">
    <h1>+ Bulk Match Import</h1>
    <p class="subtitle">One match URL per line. Use the provider that matches the URLs you paste.</p>

    <form id="match-bulk-form">
      <textarea id="urls" placeholder="https://www.fotmob.com/matches/man-city-vs-arsenal/...&#10;https://www.fotmob.com/matches/chelsea-vs-liverpool/..."></textarea>
      <div class="controls">
        <label>Provider
          <select id="provider">
            {% for key, label in providers.items() %}
            <option value="{{ key }}"
              {% if not enabled(key) %}disabled title="Not yet implemented"{% endif %}>
              {{ label }}
            </option>
            {% endfor %}
          </select>
        </label>
        <label>Workers
          <input type="number" id="workers" value="2" min="1" max="3">
        </label>
        <label>Delay (s)
          <input type="number" id="delay" value="1.0" min="0.5" max="5" step="0.5">
        </label>
        <label>Engine
          <select id="engine">
            <option value="requests">requests (default)</option>
            <option value="auto">auto (Scrapling fallback)</option>
            <option value="scrapling">scrapling</option>
          </select>
        </label>
        <button type="submit">Import</button>
        <span id="status-text" style="font-size:.82rem;color:#6b7280;"></span>
      </div>
    </form>

    <div id="progress">
      <div class="progress-bar-wrap"><div class="progress-bar" id="bar"></div></div>
      <table>
        <thead>
          <tr>
            <th>Match</th><th>Date</th><th>Score</th><th>Status</th>
          </tr>
        </thead>
        <tbody id="results-body"></tbody>
      </table>
      <div id="summary"></div>
    </div>
  </div>
</div>

<script>
document.getElementById('match-bulk-form').addEventListener('submit', async e => {
  e.preventDefault();
  const urls = document.getElementById('urls').value
    .split('\\n').map(s => s.trim()).filter(Boolean);
  if (!urls.length) return;

  const provider  = document.getElementById('provider').value;
  const workers   = document.getElementById('workers').value;
  const delay     = document.getElementById('delay').value;
  const engine    = document.getElementById('engine').value;
  const btn       = document.querySelector('button[type="submit"]');
  const statusEl  = document.getElementById('status-text');
  const progress  = document.getElementById('progress');
  const bar       = document.getElementById('bar');
  const tbody     = document.getElementById('results-body');
  const summary   = document.getElementById('summary');

  btn.disabled = true;
  progress.style.display = 'block';
  tbody.innerHTML = '';
  summary.textContent = '';
  statusEl.textContent = `Importing ${urls.length} match(es)...`;

  let done = 0, ok = 0, failed = 0, notSupported = 0;

  const resp = await fetch('/matches/bulk/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls, provider, workers: +workers, delay: +delay, engine}),
  });

  if (!resp.ok) {
    statusEl.textContent = 'Request failed — check input.';
    btn.disabled = false;
    return;
  }

  const reader  = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const {value, done: streamDone} = await reader.read();
    if (streamDone) break;
    buf += decoder.decode(value, {stream: true});
    const lines = buf.split('\\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      let ev;
      try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
      done++;
      bar.style.width = Math.round(done / urls.length * 100) + '%';

      const tr = document.createElement('tr');
      if (ev.status === 'ok') {
        ok++;
        const tdMatch = document.createElement('td');
        tdMatch.textContent = (ev.home_team || '?') + ' vs ' + (ev.away_team || '?');
        const tdDate = document.createElement('td');
        tdDate.style.color = '#6b7280';
        tdDate.textContent = ev.date || '—';
        const tdScore = document.createElement('td');
        tdScore.style.fontWeight = '700';
        tdScore.textContent = ev.score || '—';
        const tdStatus = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = 'badge-ok';
        badge.textContent = '✓ saved';
        tdStatus.appendChild(badge);
        tr.append(tdMatch, tdDate, tdScore, tdStatus);
      } else {
        if (ev.status === 'not_supported') notSupported++; else failed++;
        const tdMatch = document.createElement('td');
        const shortUrl = ev.url.length > 55 ? '…' + ev.url.slice(-52) : ev.url;
        tdMatch.textContent = shortUrl;
        tdMatch.colSpan = 3;
        const tdStatus = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = ev.status === 'not_supported' ? 'badge-ns' : 'badge-err';
        badge.textContent = ev.status === 'not_supported' ? '? not supported' : '✗ error';
        tdStatus.appendChild(badge);
        if (ev.error) {
          const errSpan = document.createElement('span');
          errSpan.style.cssText = 'font-size:.72rem;color:#4b5563;margin-left:.4rem;';
          errSpan.textContent = ev.error;
          tdStatus.appendChild(errSpan);
        }
        tr.append(tdMatch, tdStatus);
      }
      tbody.prepend(tr);
    }
  }

  statusEl.textContent = '';
  summary.textContent =
    `Done — ${ok} saved, ${notSupported} not supported, ${failed} errors.`;
  btn.disabled = false;
});
</script>
</body>
</html>
"""


@app.route("/matches/bulk")
def matches_bulk_page():
    return render_template_string(
        MATCH_BULK_TEMPLATE,
        saved_players=list_players(),
        providers=PROVIDERS,
        enabled=is_enabled,
    )


@app.route("/matches/bulk/stream", methods=["POST"])
def matches_bulk_stream():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    urls_raw = body.get("urls")
    if not isinstance(urls_raw, list):
        return jsonify({"error": "'urls' must be a list"}), 400
    urls = [u.strip() for u in urls_raw if isinstance(u, str) and str(u).strip()]
    if not urls:
        return jsonify({"error": "No valid URLs provided"}), 400

    provider = str(body.get("provider", "fotmob")).strip()
    if provider not in PROVIDERS:
        return jsonify({"error": f"Unknown provider: {provider!r}"}), 400
    if not is_enabled(provider):
        return jsonify({"error": f"Provider {provider!r} is not yet implemented"}), 400

    try:
        workers = max(1, min(int(body.get("workers", 2)), 3))
    except (TypeError, ValueError):
        return jsonify({"error": "'workers' must be an integer"}), 400

    try:
        delay = max(0.5, min(float(body.get("delay", 1.0)), 5.0))
    except (TypeError, ValueError):
        return jsonify({"error": "'delay' must be a number"}), 400

    match_engine = str(body.get("engine", "requests")).strip()
    if match_engine not in VALID_ENGINES:
        return jsonify({"error": f"Invalid engine: {match_engine!r}. Valid: {sorted(VALID_ENGINES)}"}), 400

    result_queue: queue.Queue = queue.Queue()

    def on_result(r: MatchImportResult):
        if r.status == "ok":
            result_queue.put({
                "status":    "ok",
                "url":       r.url,
                "match_id":  r.match_id,
                "home_team": r.home_team,
                "away_team": r.away_team,
                "score":     r.score,
                "date":      r.date,
            })
        else:
            result_queue.put({
                "status": r.status,
                "url":    r.url,
                "error":  r.error,
            })

    def run():
        try:
            bulk_import_matches(
                urls, provider=provider,
                workers=workers, delay=delay,
                progress_cb=on_result,
                engine=match_engine,
            )
        except Exception:
            logger.exception("bulk_import_matches raised unexpectedly")
        finally:
            result_queue.put(None)  # sentinel always sent

    threading.Thread(target=run, daemon=True).start()

    def generate():
        while True:
            item = result_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    print("Starting FotMob player stats app at http://localhost:5000")
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, port=5000, threaded=True)
