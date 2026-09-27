from __future__ import annotations

import re

INSTALL_MIN = 10_000
INSTALL_MAX = 500_000


def in_install_band(min_installs: int | None) -> bool:
    if min_installs is None:
        return False
    return INSTALL_MIN <= min_installs < INSTALL_MAX


def parse_installs(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return int(digits)


def publisher_blocked(developer: str, names: list[str]) -> bool:
    dev = developer.casefold().strip()
    if not dev:
        return False
    words = set(re.findall(r"[a-z0-9]+", dev))
    for name in names:
        token = name.casefold().strip()
        if not token:
            continue
        if len(token) < 5:
            if token in words:
                return True
        elif token in dev:
            return True
    return False


def is_free(app: dict) -> bool:
    if app.get("free") is False:
        return False
    if app.get("free") is True:
        return True
    price = app.get("price")
    return price == 0


def is_puzzle(app: dict) -> bool:
    genre_id = str(app.get("genreId") or "")
    genre = str(app.get("genre") or "").casefold()
    if genre_id == "GAME_PUZZLE":
        return True
    return any(token in genre for token in ("puzzle", "giải đố", "giai do"))


def hard_reject(app: dict, publisher_names: list[str], blocked_ids: set[str]) -> str | None:
    if not is_free(app):
        return "paid"
    installs = app.get("minInstalls")
    if not isinstance(installs, int):
        installs = parse_installs(app.get("installs"))
    if not in_install_band(installs):
        return "installs"
    if publisher_blocked(str(app.get("developer") or ""), publisher_names):
        return "publisher"
    app_id = str(app.get("appId") or app.get("app_id") or "")
    if app_id and app_id in blocked_ids:
        return "chart"
    if not is_puzzle(app):
        return "genre"
    return None
