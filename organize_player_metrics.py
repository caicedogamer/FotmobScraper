"""Organize collected player metadata into metric-focused files.

Reads players_with_meta.tsv and writes grouped summaries to data/player_metrics/.
No network calls are made.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_INPUT = Path("players_with_meta.tsv")
DEFAULT_OUTPUT = Path("data/player_metrics")

POSITION_GROUPS = {
    "GK": ("gk", "goalkeeper", "keeper"),
    "DEF": ("cb", "lb", "rb", "lwb", "rwb", "defender", "back"),
    "MID": ("cm", "dm", "cdm", "am", "cam", "lm", "rm", "midfielder", "midfield"),
    "ATT": ("st", "cf", "lw", "rw", "forward", "striker", "winger", "attack"),
}

PLAYER_FIELDS = [
    "id",
    "name",
    "team",
    "position",
    "position_group",
    "country",
    "league_key",
    "league",
]


def _slug(value: str, fallback: str = "unknown") -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return text or fallback


def _split_multi(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _position_group(position: str) -> str:
    text = f" {str(position or '').lower()} "
    tokens = set(re.split(r"[^a-z0-9]+", text))
    for group, needles in POSITION_GROUPS.items():
        if any(needle in tokens or needle in text for needle in needles):
            return group
    return "UNK"


def _read_players(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = []
        for row in csv.DictReader(f, delimiter="\t"):
            if not row.get("id") or not row.get("name"):
                continue
            clean = {field: (row.get(field) or "").strip() for field in row}
            clean["position_group"] = _position_group(clean.get("position", ""))
            rows.append(clean)
        return rows


def _write_tsv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _counter_rows(counter: Counter, key_name: str) -> list[dict]:
    return [
        {key_name: key, "players": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0]).lower()))
    ]


def _grouped_players(players: list[dict], field: str) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for player in players:
        values = _split_multi(player.get(field, "")) if field in {"league_key", "league"} else [player.get(field, "")]
        for value in values:
            grouped[value or "Unknown"].append(player)
    return grouped


def _league_key_rows(players: list[dict]) -> list[dict]:
    grouped = defaultdict(lambda: {"league_key": "", "league": set(), "players": 0})
    for player in players:
        keys = _split_multi(player.get("league_key", "")) or ["unknown"]
        names = _split_multi(player.get("league", "")) or ["Unknown"]
        for idx, key in enumerate(keys):
            grouped[key]["league_key"] = key
            grouped[key]["players"] += 1
            grouped[key]["league"].add(names[min(idx, len(names) - 1)])
    rows = []
    for row in grouped.values():
        rows.append({
            "league_key": row["league_key"],
            "league": ", ".join(sorted(row["league"])),
            "players": row["players"],
        })
    return sorted(rows, key=lambda r: (-r["players"], r["league_key"]))


def organize(input_path: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT, clean: bool = True) -> dict:
    if not input_path.exists():
        raise FileNotFoundError(f"{input_path} does not exist")
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    players = sorted(_read_players(input_path), key=lambda p: (p["name"].split()[-1].lower(), p["name"].lower()))

    _write_tsv(output_dir / "players_index.tsv", players, PLAYER_FIELDS)

    league_counter = Counter()
    for player in players:
        for league in _split_multi(player.get("league", "")) or ["Unknown"]:
            league_counter[league] += 1

    counters = {
        "leagues": _counter_rows(league_counter, "league"),
        "league_keys": _league_key_rows(players),
        "teams": _counter_rows(Counter(p.get("team") or "Unknown" for p in players), "team"),
        "countries": _counter_rows(Counter(p.get("country") or "Unknown" for p in players), "country"),
        "positions": _counter_rows(Counter(p.get("position") or "Unknown" for p in players), "position"),
        "position_groups": _counter_rows(Counter(p.get("position_group") or "UNK" for p in players), "position_group"),
    }

    for metric, rows in counters.items():
        _write_tsv(output_dir / f"{metric}.tsv", rows, list(rows[0].keys()) if rows else [metric, "players"])

    grouped_specs = {
        "by_league": "league_key",
        "by_team": "team",
        "by_country": "country",
        "by_position_group": "position_group",
    }
    for folder, field in grouped_specs.items():
        for group_name, group_rows in _grouped_players(players, field).items():
            filename = f"{_slug(group_name)}.tsv"
            _write_tsv(output_dir / folder / filename, sorted(group_rows, key=lambda p: p["name"].lower()), PLAYER_FIELDS)

    summary = {
        "input": str(input_path),
        "output": str(output_dir),
        "players": len(players),
        "leagues": len(counters["league_keys"]),
        "teams": len(counters["teams"]),
        "countries": len(counters["countries"]),
        "position_groups": {row["position_group"]: row["players"] for row in counters["position_groups"]},
        "files": {
            "player_index": "players_index.tsv",
            "metrics": [f"{name}.tsv" for name in counters],
            "groups": list(grouped_specs),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Organize player metadata into metric files")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-clean", action="store_true", help="Do not clear the output folder first")
    args = parser.parse_args()

    summary = organize(args.input, args.output, clean=not args.no_clean)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
