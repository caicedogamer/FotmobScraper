"""Small public seed helper for scripts/tests."""

from fotmob.game.db import init_game_db


def seed_game_data():
    init_game_db()
