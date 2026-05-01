"""Pack opening logic for the Discord card minigame."""

import random

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.cards import DUPLICATE_REFUNDS, RARITY_ORDER, RARITY_LABELS
from fotmob.game.db import ensure_user
from fotmob.game.odds import PACK_DEFINITIONS, choose_rarity, rarity_at_least, rarity_rank


def list_pack_types() -> list[dict]:
    return [
        {"key": key, **pack}
        for key, pack in PACK_DEFINITIONS.items()
    ]


def get_pack(pack_key: str) -> dict | None:
    pack = PACK_DEFINITIONS.get(pack_key)
    return {"key": pack_key, **pack} if pack else None


def _eligible_rarities(target_rarity: str) -> list[str]:
    idx = rarity_rank(target_rarity)
    return list(reversed(RARITY_ORDER[:idx + 1]))


def _draw_card(cur, rarity: str, min_rating: int, rng: random.Random) -> dict:
    for candidate in _eligible_rarities(rarity):
        cur.execute("""
            SELECT *
            FROM game_player_cards
            WHERE is_active AND rarity = %s AND rating >= %s
            ORDER BY RANDOM()
            LIMIT 1
        """, (candidate, min_rating))
        row = cur.fetchone()
        if row:
            return dict(row)
    cur.execute("""
        SELECT *
        FROM game_player_cards
        WHERE is_active
        ORDER BY rating ASC, RANDOM()
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        raise ValueError("No active player cards are seeded.")
    return dict(row)


def _add_to_inventory(cur, discord_id: str, card: dict) -> tuple[bool, int]:
    cur.execute("""
        SELECT *
        FROM game_inventory
        WHERE discord_id = %s AND card_id = %s
        FOR UPDATE
    """, (discord_id, card["id"]))
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE game_inventory
            SET duplicate_count = duplicate_count + 1
            WHERE id = %s
        """, (existing["id"],))
        refund = DUPLICATE_REFUNDS.get(card["rarity"], 0)
        return True, refund

    cur.execute("""
        INSERT INTO game_inventory (discord_id, card_id)
        VALUES (%s, %s)
    """, (discord_id, card["id"]))
    return False, 0


def open_pack(discord_id: str, pack_key: str) -> dict:
    pack = get_pack(pack_key)
    if not pack:
        return {"ok": False, "error": "Unknown pack."}

    rng = random.SystemRandom()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute(
                "SELECT coins FROM game_users WHERE discord_id = %s FOR UPDATE",
                (discord_id,),
            )
            user = cur.fetchone()
            balance = int(user["coins"])
            if balance < pack["price"]:
                return {
                    "ok": False,
                    "error": f"You need {pack['price']:,} coins for this pack. Balance: {balance:,}.",
                }

            cur.execute("""
                UPDATE game_users
                SET coins = coins - %s
                WHERE discord_id = %s
                RETURNING coins
            """, (pack["price"], discord_id))
            balance_after_cost = int(cur.fetchone()["coins"])

            cur.execute("""
                INSERT INTO game_pack_openings (discord_id, pack_key, cost)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (discord_id, pack_key, pack["price"]))
            opening_id = int(cur.fetchone()["id"])

            target_rarities = [
                choose_rarity(pack["odds"], rng)
                for _ in range(pack["cards_per_pack"])
            ]
            guarantee = pack.get("guaranteed_rarity")
            if guarantee and not any(rarity_at_least(r, guarantee) for r in target_rarities):
                weakest_idx = min(range(len(target_rarities)), key=lambda i: rarity_rank(target_rarities[i]))
                target_rarities[weakest_idx] = guarantee

            pulled = []
            total_refund = 0
            for rarity in target_rarities:
                card = _draw_card(cur, rarity, pack["min_rating"], rng)
                is_duplicate, refund = _add_to_inventory(cur, discord_id, card)
                total_refund += refund
                cur.execute("""
                    INSERT INTO game_pack_opening_items
                        (opening_id, card_id, is_duplicate, coins_refunded)
                    VALUES (%s, %s, %s, %s)
                """, (opening_id, card["id"], is_duplicate, refund))
                pulled.append({
                    **card,
                    "is_duplicate": is_duplicate,
                    "coins_refunded": refund,
                    "rarity_label": RARITY_LABELS.get(card["rarity"], card["rarity"].title()),
                })

            if total_refund:
                cur.execute("""
                    UPDATE game_users
                    SET coins = coins + %s
                    WHERE discord_id = %s
                    RETURNING coins
                """, (total_refund, discord_id))
                final_balance = int(cur.fetchone()["coins"])
            else:
                final_balance = balance_after_cost

    best = max(pulled, key=lambda c: (rarity_rank(c["rarity"]), c["rating"]))
    return {
        "ok": True,
        "opening_id": opening_id,
        "pack": pack,
        "cards": pulled,
        "best": best,
        "duplicates": sum(1 for c in pulled if c["is_duplicate"]),
        "coins_refunded": total_refund,
        "balance": final_balance,
    }
