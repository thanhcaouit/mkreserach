from __future__ import annotations

import json
from pathlib import Path

import yaml

from mkresearch.dedupe import normalize_title


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class SteamData:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def load_seeds(self) -> list[dict]:
        raw = yaml.safe_load((self.root / "seeds.yaml").read_text(encoding="utf-8")) or {}
        return list(raw.get("games") or [])

    def load_publishers(self) -> list[str]:
        raw = yaml.safe_load((self.root / "publishers.yaml").read_text(encoding="utf-8")) or {}
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
        return _read_json(self.root / "profile.json", {"anchor_app_ids": [], "search_queries": []})

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

    def load_seen(self) -> dict:
        return _read_json(self.root / "seen.json", {"apps": {}})

    def save_seen(self, seen: dict) -> None:
        _write_json(self.root / "seen.json", seen)

    def load_cooldown(self) -> dict:
        return _read_json(self.root / "cooldown.json", {})

    def save_cooldown(self, state: dict) -> None:
        _write_json(self.root / "cooldown.json", state)

    def load_play_reference(self) -> tuple[dict, list[dict]]:
        play = self.root.parent
        catalog = _read_json(play / "catalog.json", {"apps": {}})
        seed_path = play / "seeds.yaml"
        if not seed_path.exists():
            return catalog, []
        raw = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
        return catalog, list(raw.get("games") or [])


def titles_of(catalog: dict, seeds: list[dict]) -> set[str]:
    titles: set[str] = set()
    for app in (catalog.get("apps") or {}).values():
        norm = app.get("title_norm") or normalize_title(str(app.get("title") or ""))
        if norm:
            titles.add(norm)
    for seed in seeds:
        norm = normalize_title(str(seed.get("title") or ""))
        if norm:
            titles.add(norm)
    return titles


def already_sent(
    app_id: str,
    title: str,
    steam_catalog: dict,
    steam_seeds: list[dict],
    play_catalog: dict,
    play_seeds: list[dict],
) -> bool:
    apps = steam_catalog.get("apps") or {}
    seed_ids = {str(seed.get("app_id")) for seed in steam_seeds}
    if app_id in apps or app_id in seed_ids:
        return True
    norm = normalize_title(title)
    if not norm:
        return False
    seen = titles_of(steam_catalog, steam_seeds) | titles_of(play_catalog, play_seeds)
    return norm in seen
