"""Inventory, collection, quick-sell, and leaderboard helpers."""

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.cards import DUPLICATE_REFUNDS, RARITY_ORDER
from fotmob.game.db import ensure_user


def list_inventory(discord_id: str, rarity: str | None = None, position: str | None = None, limit: int = 20):
    clauses = ["i.discord_id = %s"]
    params = [discord_id]
    if rarity:
        clauses.append("c.rarity = %s")
        params.append(rarity.lower())
    if position:
        clauses.append("LOWER(c.position) = %s")
        params.append(position.lower())

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute(f"""
                SELECT i.id AS inventory_id, i.duplicate_count, i.locked,
                       c.id AS card_id, c.name, c.club, c.nationality,
                       c.position, c.rating, c.rarity, c.image_url, c.card_type
                FROM game_inventory i
                JOIN game_player_cards c ON c.id = i.card_id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.rating DESC, c.name
                LIMIT %s
            """, (*params, limit))
            return [dict(r) for r in cur.fetchall()]


def collection_summary(discord_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute("""
                SELECT rarity, COUNT(*) AS total
                FROM game_player_cards
                WHERE is_active
                GROUP BY rarity
            """)
            total_by_rarity = {r["rarity"]: int(r["total"]) for r in cur.fetchall()}
            cur.execute("""
                SELECT c.rarity, COUNT(*) AS owned
                FROM game_inventory i
                JOIN game_player_cards c ON c.id = i.card_id
                WHERE i.discord_id = %s
                GROUP BY c.rarity
            """, (discord_id,))
            owned_by_rarity = {r["rarity"]: int(r["owned"]) for r in cur.fetchall()}

    total = sum(total_by_rarity.values())
    owned = sum(owned_by_rarity.values())
    rows = []
    for rarity in RARITY_ORDER:
        r_total = total_by_rarity.get(rarity, 0)
        r_owned = owned_by_rarity.get(rarity, 0)
        pct = round(r_owned / r_total * 100, 1) if r_total else 0
        rows.append({"rarity": rarity, "owned": r_owned, "total": r_total, "pct": pct})
    return {
        "owned": owned,
        "total": total,
        "pct": round(owned / total * 100, 1) if total else 0,
        "rarities": rows,
    }


def quick_sell(discord_id: str, inventory_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute("""
                SELECT i.*, c.name, c.rarity, c.rating
                FROM game_inventory i
                JOIN game_player_cards c ON c.id = i.card_id
                WHERE i.id = %s AND i.discord_id = %s
                FOR UPDATE
            """, (inventory_id, discord_id))
            item = cur.fetchone()
            if not item:
                return {"ok": False, "error": "Inventory item not found."}
            if item["locked"]:
                return {"ok": False, "error": "That card is locked."}

            refund = DUPLICATE_REFUNDS.get(item["rarity"], 0)
            if item["duplicate_count"] > 0:
                cur.execute("""
                    UPDATE game_inventory
                    SET duplicate_count = duplicate_count - 1
                    WHERE id = %s
                """, (inventory_id,))
                sold_copy = "duplicate"
            else:
                cur.execute("DELETE FROM game_inventory WHERE id = %s", (inventory_id,))
                sold_copy = "card"

            cur.execute("""
                UPDATE game_users
                SET coins = coins + %s
                WHERE discord_id = %s
                RETURNING coins
            """, (refund, discord_id))
            balance = int(cur.fetchone()["coins"])
            return {
                "ok": True,
                "name": item["name"],
                "rarity": item["rarity"],
                "rating": item["rating"],
                "refund": refund,
                "balance": balance,
                "sold_copy": sold_copy,
            }


def leaderboard(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT u.discord_id, u.coins,
                       COUNT(i.id) AS unique_cards,
                       COALESCE(SUM(c.rating), 0) AS collection_value
                FROM game_users u
                LEFT JOIN game_inventory i ON i.discord_id = u.discord_id
                LEFT JOIN game_player_cards c ON c.id = i.card_id
                GROUP BY u.discord_id, u.coins
                ORDER BY collection_value DESC, unique_cards DESC, u.coins DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
