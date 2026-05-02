"""Squad building helpers for the Discord card minigame."""
from __future__ import annotations

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.db import ensure_user


# ── Formation definitions ─────────────────────────────────────────────────────
# Each slot: key (unique ID), label (display position), x/y normalised [0,1].
# x=0 → left touchline, x=1 → right touchline.
# y=0 → GK end (bottom of render), y=1 → striker end (top of render).

FORMATIONS: dict[str, list[dict]] = {
    "4-3-3": [
        {"key": "GK",   "label": "GK",  "x": 0.50, "y": 0.05},
        {"key": "LB",   "label": "LB",  "x": 0.12, "y": 0.26},
        {"key": "LCB",  "label": "CB",  "x": 0.36, "y": 0.21},
        {"key": "RCB",  "label": "CB",  "x": 0.64, "y": 0.21},
        {"key": "RB",   "label": "RB",  "x": 0.88, "y": 0.26},
        {"key": "LCM",  "label": "CM",  "x": 0.25, "y": 0.52},
        {"key": "CM",   "label": "CM",  "x": 0.50, "y": 0.52},
        {"key": "RCM",  "label": "CM",  "x": 0.75, "y": 0.52},
        {"key": "LW",   "label": "LW",  "x": 0.12, "y": 0.80},
        {"key": "ST",   "label": "ST",  "x": 0.50, "y": 0.86},
        {"key": "RW",   "label": "RW",  "x": 0.88, "y": 0.80},
    ],
    "4-2-3-1": [
        {"key": "GK",   "label": "GK",  "x": 0.50, "y": 0.05},
        {"key": "LB",   "label": "LB",  "x": 0.12, "y": 0.26},
        {"key": "LCB",  "label": "CB",  "x": 0.36, "y": 0.21},
        {"key": "RCB",  "label": "CB",  "x": 0.64, "y": 0.21},
        {"key": "RB",   "label": "RB",  "x": 0.88, "y": 0.26},
        {"key": "LCDM", "label": "CDM", "x": 0.35, "y": 0.47},
        {"key": "RCDM", "label": "CDM", "x": 0.65, "y": 0.47},
        {"key": "LAM",  "label": "LM",  "x": 0.18, "y": 0.70},
        {"key": "CAM",  "label": "CAM", "x": 0.50, "y": 0.72},
        {"key": "RAM",  "label": "RM",  "x": 0.82, "y": 0.70},
        {"key": "ST",   "label": "ST",  "x": 0.50, "y": 0.88},
    ],
    "4-4-2": [
        {"key": "GK",   "label": "GK",  "x": 0.50, "y": 0.05},
        {"key": "LB",   "label": "LB",  "x": 0.12, "y": 0.26},
        {"key": "LCB",  "label": "CB",  "x": 0.36, "y": 0.21},
        {"key": "RCB",  "label": "CB",  "x": 0.64, "y": 0.21},
        {"key": "RB",   "label": "RB",  "x": 0.88, "y": 0.26},
        {"key": "LM",   "label": "LM",  "x": 0.12, "y": 0.54},
        {"key": "LCM",  "label": "CM",  "x": 0.38, "y": 0.54},
        {"key": "RCM",  "label": "CM",  "x": 0.62, "y": 0.54},
        {"key": "RM",   "label": "RM",  "x": 0.88, "y": 0.54},
        {"key": "LST",  "label": "ST",  "x": 0.36, "y": 0.85},
        {"key": "RST",  "label": "ST",  "x": 0.64, "y": 0.85},
    ],
    "3-5-2": [
        {"key": "GK",   "label": "GK",  "x": 0.50, "y": 0.05},
        {"key": "LCB",  "label": "CB",  "x": 0.22, "y": 0.21},
        {"key": "CB",   "label": "CB",  "x": 0.50, "y": 0.19},
        {"key": "RCB",  "label": "CB",  "x": 0.78, "y": 0.21},
        {"key": "LWB",  "label": "LWB", "x": 0.09, "y": 0.49},
        {"key": "LCM",  "label": "CM",  "x": 0.30, "y": 0.52},
        {"key": "CDM",  "label": "CDM", "x": 0.50, "y": 0.47},
        {"key": "RCM",  "label": "CM",  "x": 0.70, "y": 0.52},
        {"key": "RWB",  "label": "RWB", "x": 0.91, "y": 0.49},
        {"key": "LST",  "label": "ST",  "x": 0.36, "y": 0.85},
        {"key": "RST",  "label": "ST",  "x": 0.64, "y": 0.85},
    ],
}

VALID_FORMATIONS: frozenset[str] = frozenset(FORMATIONS)
DEFAULT_FORMATION = "4-3-3"


def _slot_keys(formation: str) -> set[str]:
    return {s["key"] for s in FORMATIONS.get(formation, [])}


# ── Database helpers ──────────────────────────────────────────────────────────

def init_squad_tables(cur) -> None:
    """Create game_squads and game_squad_slots tables if they don't exist."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_squads (
            discord_id  TEXT PRIMARY KEY
                REFERENCES game_users(discord_id) ON DELETE CASCADE,
            formation   TEXT NOT NULL DEFAULT '4-3-3',
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS game_squad_slots (
            id           SERIAL PRIMARY KEY,
            discord_id   TEXT NOT NULL
                REFERENCES game_squads(discord_id) ON DELETE CASCADE,
            slot_key     TEXT NOT NULL,
            inventory_id INTEGER
                REFERENCES game_inventory(id) ON DELETE SET NULL,
            UNIQUE (discord_id, slot_key)
        );

        CREATE INDEX IF NOT EXISTS idx_squad_slots_user
            ON game_squad_slots(discord_id);
    """)


def get_squad(discord_id: str) -> dict:
    """
    Return the user's current squad.

    Returns::

        {
            "formation": "4-3-3",
            "slots": {
                "GK": {"name": ..., "rating": ..., "rarity": ...,
                        "position": ..., "club": ..., "inventory_id": ...},
                "LB": None,   # empty slot
                ...
            }
        }
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)

            cur.execute(
                "SELECT formation FROM game_squads WHERE discord_id = %s",
                (discord_id,),
            )
            row = cur.fetchone()
            formation = row["formation"] if row else DEFAULT_FORMATION

            cur.execute("""
                SELECT ss.slot_key, ss.inventory_id,
                       c.name, c.rating, c.rarity, c.position, c.club
                FROM game_squad_slots ss
                LEFT JOIN game_inventory i ON i.id = ss.inventory_id
                LEFT JOIN game_player_cards c ON c.id = i.card_id
                WHERE ss.discord_id = %s
            """, (discord_id,))
            raw_slots = {r["slot_key"]: r for r in cur.fetchall()}

    slots: dict[str, dict | None] = {}
    for slot_def in FORMATIONS.get(formation, []):
        key = slot_def["key"]
        row = raw_slots.get(key)
        if row and row["name"]:
            slots[key] = {
                "name":         row["name"],
                "rating":       row["rating"],
                "rarity":       row["rarity"],
                "position":     row["position"],
                "club":         row["club"],
                "inventory_id": row["inventory_id"],
            }
        else:
            slots[key] = None

    return {"formation": formation, "slots": slots}


def set_formation(discord_id: str, formation: str) -> dict:
    """Set the user's formation, clearing slots that don't exist in the new formation."""
    if formation not in VALID_FORMATIONS:
        return {"ok": False, "error": f"Unknown formation. Valid: {sorted(VALID_FORMATIONS)}"}

    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user(cur, discord_id)

            cur.execute("""
                INSERT INTO game_squads (discord_id, formation, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (discord_id) DO UPDATE
                    SET formation  = EXCLUDED.formation,
                        updated_at = NOW()
            """, (discord_id, formation))

            valid_keys = list(_slot_keys(formation))
            cur.execute("""
                DELETE FROM game_squad_slots
                WHERE discord_id = %s AND NOT (slot_key = ANY(%s))
            """, (discord_id, valid_keys))

    return {"ok": True, "formation": formation}


def place_player(discord_id: str, slot_key: str, inventory_id: int) -> dict:
    """Assign an owned inventory card to a formation slot."""
    slot_key = slot_key.upper().strip()

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)

            cur.execute(
                "SELECT formation FROM game_squads WHERE discord_id = %s",
                (discord_id,),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("""
                    INSERT INTO game_squads (discord_id, formation)
                    VALUES (%s, %s)
                    ON CONFLICT (discord_id) DO NOTHING
                """, (discord_id, DEFAULT_FORMATION))
                formation = DEFAULT_FORMATION
            else:
                formation = row["formation"]

            if slot_key not in _slot_keys(formation):
                valid = sorted(_slot_keys(formation))
                return {
                    "ok": False,
                    "error": (
                        f"Slot `{slot_key}` does not exist in `{formation}`. "
                        f"Valid slots: {', '.join(valid)}"
                    ),
                }

            cur.execute("""
                SELECT i.id AS inventory_id,
                       c.name, c.rating, c.rarity, c.position, c.club
                FROM game_inventory i
                JOIN game_player_cards c ON c.id = i.card_id
                WHERE i.id = %s AND i.discord_id = %s
            """, (inventory_id, discord_id))
            card = cur.fetchone()
            if not card:
                return {"ok": False, "error": "You don't own that card (check the inventory_id)."}

            cur.execute("""
                SELECT slot_key FROM game_squad_slots
                WHERE discord_id = %s AND inventory_id = %s AND slot_key <> %s
            """, (discord_id, inventory_id, slot_key))
            conflict = cur.fetchone()
            if conflict:
                return {
                    "ok": False,
                    "error": (
                        f"That card is already placed in slot `{conflict['slot_key']}`. "
                        f"Remove it first with `/squad_remove position:{conflict['slot_key']}`."
                    ),
                }

            cur.execute("""
                INSERT INTO game_squad_slots (discord_id, slot_key, inventory_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (discord_id, slot_key) DO UPDATE
                    SET inventory_id = EXCLUDED.inventory_id
            """, (discord_id, slot_key, inventory_id))

    return {
        "ok":     True,
        "slot":   slot_key,
        "name":   card["name"],
        "rating": card["rating"],
        "rarity": card["rarity"],
    }


def remove_player(discord_id: str, slot_key: str) -> dict:
    """Clear a single slot in the user's squad."""
    slot_key = slot_key.upper().strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user(cur, discord_id)
            cur.execute(
                "DELETE FROM game_squad_slots WHERE discord_id = %s AND slot_key = %s",
                (discord_id, slot_key),
            )
            removed = cur.rowcount > 0
    return {"ok": True, "removed": removed, "slot": slot_key}


def clear_squad(discord_id: str) -> None:
    """Remove all cards from the user's squad (keeps the formation)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user(cur, discord_id)
            cur.execute(
                "DELETE FROM game_squad_slots WHERE discord_id = %s",
                (discord_id,),
            )
