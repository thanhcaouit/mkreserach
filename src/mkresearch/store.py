from __future__ import annotations

import json
from pathlib import Path

import yaml


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def load_seeds(self) -> list[dict]:
        path = self.root / "seeds.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return list(raw.get("games") or [])

    def load_publishers(self) -> list[str]:
        path = self.root / "publishers.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [str(name) for name in raw.get("names") or []]

    def load_catalog(self) -> dict:
        return _read_json(self.root / "catalog.json", {"apps": {}})

    def save_catalog(self, catalog: dict) -> None:
        _write_json(self.root / "catalog.json", catalog)

    def load_ratings(self) -> dict:
        return _read_json(self.root / "ratings.json", {"items": []})

    def save_ratings(self, ratings: dict) -> None:
        _write_json(self.root / "ratings.json", ratings)

    def load_profile(self) -> dict:
        return _read_json(
            self.root / "profile.json",
            {
                "liked_mechanics": [],
                "disliked_mechanics": [],
                "search_queries": [],
                "anchor_app_ids": [],
                "updated_at": None,
            },
        )

    def save_profile(self, profile: dict) -> None:
        _write_json(self.root / "profile.json", profile)

    def load_blocklist(self) -> dict:
        return _read_json(self.root / "chart_blocklist.json", {"apps": {}})

    def save_blocklist(self, blocklist: dict) -> None:
        _write_json(self.root / "chart_blocklist.json", blocklist)

    def load_offset(self) -> int:
        raw = _read_json(self.root / "telegram_offset.json", {"offset": 0})
        return int(raw.get("offset") or 0)

    def save_offset(self, offset: int) -> None:
        _write_json(self.root / "telegram_offset.json", {"offset": offset})
