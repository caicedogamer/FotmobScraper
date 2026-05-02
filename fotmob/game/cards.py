"""Card rarity rules and seed player data for the Discord minigame."""

import csv
import hashlib
from pathlib import Path

RARITY_ORDER = ["common", "uncommon", "rare", "elite", "legendary", "mythic"]

RARITY_COLORS = {
    "common": 0x95a5a6,
    "uncommon": 0x2ecc71,
    "rare": 0x3498db,
    "elite": 0x9b59b6,
    "legendary": 0xf1c40f,
    "mythic": 0xff4dd2,
}

RARITY_LABELS = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "elite": "Elite",
    "legendary": "Legendary",
    "mythic": "Mythic",
}

DUPLICATE_REFUNDS = {
    "common": 10,
    "uncommon": 25,
    "rare": 75,
    "elite": 250,
    "legendary": 1000,
    "mythic": 3000,
}


def rarity_for_rating(rating: int) -> str:
    if rating >= 93:
        return "mythic"
    if rating >= 88:
        return "legendary"
    if rating >= 83:
        return "elite"
    if rating >= 75:
        return "rare"
    if rating >= 65:
        return "uncommon"
    return "common"


SEED_PLAYERS = [
    ("Erling Haaland", "Manchester City", "Norway", "ST", 91),
    ("Kylian Mbappe", "Real Madrid", "France", "ST", 92),
    ("Jude Bellingham", "Real Madrid", "England", "CM", 90),
    ("Vinicius Junior", "Real Madrid", "Brazil", "LW", 90),
    ("Rodri", "Manchester City", "Spain", "CDM", 91),
    ("Kevin De Bruyne", "Manchester City", "Belgium", "CM", 89),
    ("Mohamed Salah", "Liverpool", "Egypt", "RW", 89),
    ("Harry Kane", "Bayern Munich", "England", "ST", 90),
    ("Lautaro Martinez", "Inter", "Argentina", "ST", 88),
    ("Bukayo Saka", "Arsenal", "England", "RW", 87),
    ("Florian Wirtz", "Bayer Leverkusen", "Germany", "CAM", 87),
    ("Jamal Musiala", "Bayern Munich", "Germany", "CAM", 87),
    ("Phil Foden", "Manchester City", "England", "CAM", 88),
    ("Martin Odegaard", "Arsenal", "Norway", "CAM", 87),
    ("Bruno Fernandes", "Manchester United", "Portugal", "CAM", 87),
    ("Declan Rice", "Arsenal", "England", "CDM", 87),
    ("Federico Valverde", "Real Madrid", "Uruguay", "CM", 87),
    ("Bernardo Silva", "Manchester City", "Portugal", "CM", 88),
    ("Virgil van Dijk", "Liverpool", "Netherlands", "CB", 89),
    ("William Saliba", "Arsenal", "France", "CB", 86),
    ("Ruben Dias", "Manchester City", "Portugal", "CB", 88),
    ("Alisson", "Liverpool", "Brazil", "GK", 89),
    ("Thibaut Courtois", "Real Madrid", "Belgium", "GK", 90),
    ("Mike Maignan", "AC Milan", "France", "GK", 87),
    ("Trent Alexander-Arnold", "Liverpool", "England", "RB", 86),
    ("Achraf Hakimi", "Paris Saint-Germain", "Morocco", "RB", 85),
    ("Theo Hernandez", "AC Milan", "France", "LB", 86),
    ("Antoine Griezmann", "Atletico Madrid", "France", "CF", 88),
    ("Victor Osimhen", "Napoli", "Nigeria", "ST", 87),
    ("Rafael Leao", "AC Milan", "Portugal", "LW", 86),
    ("Son Heung-min", "Tottenham Hotspur", "South Korea", "LW", 87),
    ("Cole Palmer", "Chelsea", "England", "CAM", 85),
    ("Luis Diaz", "Liverpool", "Colombia", "LW", 84),
    ("Darwin Nunez", "Liverpool", "Uruguay", "ST", 83),
    ("Kai Havertz", "Arsenal", "Germany", "CF", 83),
    ("Alexis Mac Allister", "Liverpool", "Argentina", "CM", 84),
    ("Enzo Fernandez", "Chelsea", "Argentina", "CM", 83),
    ("Sandro Tonali", "Newcastle United", "Italy", "CM", 84),
    ("Moussa Diaby", "Aston Villa", "France", "RW", 82),
    ("Dominik Szoboszlai", "Liverpool", "Hungary", "CAM", 82),
    ("Ollie Watkins", "Aston Villa", "England", "ST", 83),
    ("Alexander Isak", "Newcastle United", "Sweden", "ST", 85),
    ("James Maddison", "Tottenham Hotspur", "England", "CAM", 82),
    ("Micky van de Ven", "Tottenham Hotspur", "Netherlands", "CB", 81),
    ("Kaoru Mitoma", "Brighton", "Japan", "LW", 81),
    ("Eberechi Eze", "Crystal Palace", "England", "CAM", 81),
    ("Michael Olise", "Bayern Munich", "France", "RW", 82),
    ("Ivan Toney", "Al Ahli", "England", "ST", 81),
    ("Pedro Neto", "Chelsea", "Portugal", "RW", 80),
    ("Morgan Gibbs-White", "Nottingham Forest", "England", "CAM", 79),
    ("Giorgio Scalvini", "Atalanta", "Italy", "CB", 78),
    ("Benjamin Sesko", "RB Leipzig", "Slovenia", "ST", 79),
    ("Antonio Nusa", "RB Leipzig", "Norway", "LW", 74),
    ("Kobbie Mainoo", "Manchester United", "England", "CM", 76),
    ("Endrick", "Real Madrid", "Brazil", "ST", 77),
    ("Pau Cubarsi", "Barcelona", "Spain", "CB", 76),
    ("Marc Guiu", "Chelsea", "Spain", "ST", 67),
    ("Archie Gray", "Tottenham Hotspur", "England", "CM", 72),
    ("Oscar Bobb", "Manchester City", "Norway", "RW", 73),
    ("Jorrel Hato", "Ajax", "Netherlands", "CB", 75),
    ("Roony Bardghji", "FC Copenhagen", "Sweden", "RW", 70),
]


LEAGUE_COMMON_BASE_RATINGS = {
    "premier_league": 60,
    "la_liga": 60,
    "serie_a": 60,
    "bundesliga": 59,
    "ligue_1": 59,
    "brasileirao": 57,
    "liga_portugal": 56,
    "super_lig": 56,
}


def _metadata_rating(player_id: str, league_key: str) -> int:
    """Create a stable low-tier rating for metadata-only cards."""
    primary_league = (league_key or "").split(",")[0]
    base = LEAGUE_COMMON_BASE_RATINGS.get(primary_league, 55)
    digest = hashlib.sha1(str(player_id).encode("utf-8")).hexdigest()
    return min(64, base + int(digest[:2], 16) % 5)


def metadata_card_dicts(limit: int | None = None) -> list[dict]:
    """Build a broad common card pool from collected real player metadata."""
    path = Path(__file__).resolve().parents[2] / "players_with_meta.tsv"
    if not path.exists():
        return []

    base_names = {name.lower() for name, *_ in SEED_PLAYERS}
    cards = []
    seen = set()
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            name = (row.get("name") or "").strip()
            club = (row.get("team") or "").strip()
            player_id = str(row.get("id") or "").strip()
            if not name or not club or not player_id:
                continue
            if name.lower() in base_names:
                continue
            key = (name.lower(), club.lower())
            if key in seen:
                continue
            seen.add(key)

            rating = _metadata_rating(player_id, row.get("league_key") or "")
            cards.append({
                "player_source_id": int(player_id) if player_id.isdigit() else None,
                "name": name,
                "club": club,
                "nationality": row.get("country") or None,
                "position": row.get("position") or None,
                "rating": rating,
                "rarity": rarity_for_rating(rating),
                "card_type": "metadata",
                "image_url": None,
            })
            if limit and len(cards) >= limit:
                break
    return cards


def seed_card_dicts() -> list[dict]:
    cards = []
    for name, club, nationality, position, rating in SEED_PLAYERS:
        cards.append({
            "player_source_id": None,
            "name": name,
            "club": club,
            "nationality": nationality,
            "position": position,
            "rating": rating,
            "rarity": rarity_for_rating(rating),
            "card_type": "base",
            "image_url": None,
        })
    cards.extend(metadata_card_dicts())
    return cards
