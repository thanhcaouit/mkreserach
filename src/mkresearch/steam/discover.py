from __future__ import annotations

import re

from mkresearch.dedupe import normalize_title

SEARCH_URL = "https://store.steampowered.com/search/results/"
APP_URL = "https://store.steampowered.com/api/appdetails"
PUZZLE_TAG = "1664"
APP_ID_RE = re.compile(r"/apps/(\d+)/")
DETAIL_CAP = 20


class DetailLimit(RuntimeError):
    pass


def app_id_from_logo(logo: str) -> str:
    match = APP_ID_RE.search(logo or "")
    return match.group(1) if match else ""


def parse_search_items(payload: dict) -> list[dict]:
    found: list[dict] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        app_id = str(item.get("id") or "") or app_id_from_logo(str(item.get("logo") or ""))
        title = str(item.get("name") or "").strip()
        if not app_id or not title:
            continue
        found.append({"appId": app_id, "title": title, "from_puzzle_tag": True})
    return found


def parse_app_details(app_id: str, payload: dict) -> dict | None:
    entry = payload.get(str(app_id)) or payload.get(app_id) or {}
    if not entry.get("success"):
        return None
    data = entry.get("data") or {}
    if not isinstance(data, dict):
        return None
    developers = [str(name) for name in data.get("developers") or []]
    publishers = [str(name) for name in data.get("publishers") or []]
    genres = [str(item.get("description") or "") for item in data.get("genres") or [] if isinstance(item, dict)]
    shots = []
    for shot in data.get("screenshots") or []:
        if isinstance(shot, dict) and str(shot.get("path_full") or "").startswith("http"):
            shots.append(shot["path_full"])
    price = data.get("price_overview") or {}
    reviews = (data.get("recommendations") or {}).get("total")
    return {
        "appId": str(app_id),
        "title": data.get("name") or "",
        "developer": developers[0] if developers else (publishers[0] if publishers else ""),
        "publishers": publishers,
        "reviews": reviews if isinstance(reviews, int) else None,
        "is_free": bool(data.get("is_free")),
        "price_text": str(price.get("final_formatted") or ""),
        "type": str(data.get("type") or ""),
        "coming_soon": bool((data.get("release_date") or {}).get("coming_soon")),
        "genres": genres,
        "from_puzzle_tag": True,
        "screenshots": shots,
        "description": str(data.get("short_description") or ""),
    }


def merge_blocklist(current: dict, app_ids: list[str], day: str) -> dict:
    apps = dict(current.get("apps") or {})
    for app_id in app_ids:
        apps[str(app_id)] = {"app_id": str(app_id), "seen": day}
    return {"apps": apps}


def blocked_ids(blocklist: dict) -> set[str]:
    return set((blocklist.get("apps") or {}).keys())


def seen_ids(seen: dict) -> set[str]:
    return {str(app_id) for app_id in (seen.get("apps") or {}) if str(app_id)}


def remember_seen(seen: dict, app_ids: list[str], day: str) -> dict:
    apps = dict(seen.get("apps") or {})
    for app_id in app_ids:
        token = str(app_id or "")
        if not token or token in apps:
            continue
        apps[token] = {"seen": day}
    return {"apps": apps}


def recent_match_titles(catalog: dict, limit: int = 6) -> list[str]:
    apps = list((catalog.get("apps") or {}).values())
    apps.sort(key=lambda item: str(item.get("suggested_at") or ""), reverse=True)
    titles: list[str] = []
    for app in apps:
        title = str(app.get("title") or "").strip()
        if not title or title in titles:
            continue
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles
