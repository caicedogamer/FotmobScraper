"""Pack odds and weighted rarity selection."""

import random

from fotmob.game.cards import RARITY_ORDER

PACK_DEFINITIONS = {
    "starter_pack": {
        "name": "Starter Pack",
        "price": 500,
        "cards_per_pack": 3,
        "min_rating": 50,
        "guaranteed_rarity": None,
        "description": "A cheap starter pack with mostly common cards.",
        "odds": {"common": 70, "uncommon": 25, "rare": 5},
    },
    "club_pack": {
        "name": "Club Pack",
        "price": 1000,
        "cards_per_pack": 4,
        "min_rating": 60,
        "guaranteed_rarity": "uncommon",
        "description": "Balanced pack with one uncommon or better guaranteed.",
        "odds": {"common": 50, "uncommon": 35, "rare": 12, "elite": 3},
    },
    "premium_pack": {
        "name": "Premium Pack",
        "price": 2500,
        "cards_per_pack": 5,
        "min_rating": 65,
        "guaranteed_rarity": "rare",
        "description": "Better odds and one rare or better guaranteed.",
        "odds": {"common": 35, "uncommon": 35, "rare": 20, "elite": 8, "legendary": 2},
    },
    "elite_pack": {
        "name": "Elite Pack",
        "price": 6000,
        "cards_per_pack": 5,
        "min_rating": 75,
        "guaranteed_rarity": "elite",
        "description": "High-end pack built around rare and elite cards.",
        "odds": {"rare": 55, "elite": 35, "legendary": 9, "mythic": 1},
    },
    "legend_pack": {
        "name": "Legend Pack",
        "price": 15000,
        "cards_per_pack": 4,
        "min_rating": 83,
        "guaranteed_rarity": "legendary",
        "description": "Premium chase pack with legendary and mythic odds.",
        "odds": {"elite": 60, "legendary": 35, "mythic": 5},
    },
}


def rarity_rank(rarity: str) -> int:
    return RARITY_ORDER.index(rarity)


def rarity_at_least(rarity: str, minimum: str) -> bool:
    return rarity_rank(rarity) >= rarity_rank(minimum)


def choose_rarity(odds: dict[str, int], rng: random.Random | None = None) -> str:
    rng = rng or random
    total = sum(max(0, weight) for weight in odds.values())
    if total <= 0:
        raise ValueError("Pack odds must have positive total weight")
    roll = rng.uniform(0, total)
    running = 0
    for rarity, weight in odds.items():
        running += max(0, weight)
        if roll <= running:
            return rarity
    return next(reversed(odds))


def format_odds(pack_key: str) -> str:
    pack = PACK_DEFINITIONS[pack_key]
    total = sum(pack["odds"].values())
    return "\n".join(
        f"{rarity.title()}: **{weight / total * 100:.1f}%**"
        for rarity, weight in pack["odds"].items()
    )
