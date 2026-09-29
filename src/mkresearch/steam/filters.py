from __future__ import annotations

import re

REVIEW_MIN = 0
REVIEW_MAX = 20_000


def in_review_band(reviews: int | None) -> bool:
    if reviews is None:
        return True
    return REVIEW_MIN <= reviews <= REVIEW_MAX


def publisher_blocked(names_on_app: list[str], blocked: list[str]) -> bool:
    for raw in names_on_app:
        dev = raw.casefold().strip()
        if not dev:
            continue
        words = set(re.findall(r"[a-z0-9]+", dev))
        for name in blocked:
            token = name.casefold().strip()
            if not token:
                continue
            if len(token) < 5:
                if token in words:
                    return True
            elif token in dev:
                return True
    return False


def _tag_text(value: str) -> str:
    return value.casefold().replace("-", " ").strip()


def tag_blocked(labels: list[str], ignored: list[str]) -> bool:
    names = [_tag_text(item) for item in labels if _tag_text(str(item))]
    for raw in ignored:
        needle = _tag_text(raw)
        if needle and any(needle in name for name in names):
            return True
    return False


def is_puzzle(app: dict) -> bool:
    if app.get("from_puzzle_tag"):
        return True
    genres = app.get("genres") or []
    if isinstance(genres, str):
        genres = [genres]
    text = " ".join(str(item) for item in genres).casefold()
    return "puzzle" in text


def hard_reject(
    app: dict,
    publisher_names: list[str],
    blocked_ids: set[str],
    ignore_tags: list[str] | None = None,
) -> str | None:
    if str(app.get("type") or "game") != "game":
        return "type"
    if app.get("coming_soon"):
        return "unreleased"
    reviews = app.get("reviews")
    if not isinstance(reviews, int):
        reviews = None
    if not in_review_band(reviews):
        return "reviews"
    parties = [str(app.get("developer") or "")]
    parties.extend(str(name) for name in app.get("publishers") or [])
    if publisher_blocked(parties, publisher_names):
        return "publisher"
    app_id = str(app.get("appId") or "")
    if app_id and app_id in blocked_ids:
        return "chart"
    labels = [str(item) for item in app.get("genres") or []]
    labels.extend(str(item) for item in app.get("tags") or [])
    if tag_blocked(labels, ignore_tags or []):
        return "tag"
    if not is_puzzle(app):
        return "genre"
    return None
