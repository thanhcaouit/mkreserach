from __future__ import annotations

import re


def normalize_title(title: str) -> str:
    text = title.casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _title_set(catalog: dict, seeds: list[dict]) -> set[str]:
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


def is_duplicate(package_id: str, title: str, catalog: dict, seeds: list[dict]) -> bool:
    apps = catalog.get("apps") or {}
    seed_ids = {str(seed.get("app_id")) for seed in seeds}
    if package_id in apps or package_id in seed_ids:
        return True
    norm = normalize_title(title)
    if norm and norm in _title_set(catalog, seeds):
        return True
    return False
