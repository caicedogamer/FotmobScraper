"""Coin economy helpers for the Discord card minigame."""

from datetime import datetime, timezone

import psycopg2.extras

from fotmob.db import get_conn
from fotmob.game.db import ensure_user

STARTING_COINS = 2500
DAILY_COINS = 1000
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60


def get_balance(discord_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute("SELECT coins FROM game_users WHERE discord_id = %s", (discord_id,))
            return int(cur.fetchone()["coins"])


def claim_daily(discord_id: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ensure_user(cur, discord_id)
            cur.execute(
                "SELECT coins, last_daily_at FROM game_users WHERE discord_id = %s FOR UPDATE",
                (discord_id,),
            )
            user = cur.fetchone()
            last_daily = user["last_daily_at"]
            if last_daily:
                elapsed = (now - last_daily).total_seconds()
                if elapsed < DAILY_COOLDOWN_SECONDS:
                    return {
                        "claimed": False,
                        "balance": int(user["coins"]),
                        "remaining_seconds": int(DAILY_COOLDOWN_SECONDS - elapsed),
                    }

            new_balance = int(user["coins"]) + DAILY_COINS
            cur.execute("""
                UPDATE game_users
                SET coins = %s, last_daily_at = NOW()
                WHERE discord_id = %s
            """, (new_balance, discord_id))
            return {
                "claimed": True,
                "amount": DAILY_COINS,
                "balance": new_balance,
                "remaining_seconds": 0,
            }
