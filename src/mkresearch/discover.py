from __future__ import annotations

import re
import time

from google_play_scraper import app as fetch_app
from google_play_scraper import search as fetch_search
from google_play_scraper.exceptions import NotFoundError
from google_play_scraper.utils.request import get

from mkresearch.cooldown import Guard, limit_kind
from mkresearch.dedupe import is_duplicate
from mkresearch.filters import (
    hard_reject,
    in_install_band,
    is_puzzle,
    parse_installs,
    publisher_blocked,
    strip_ignored,
    title_ignored,
)

APP_ID_RE = re.compile(r"id=([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)")
SKIP_PREFIXES = ("com.google.android", "com.android.", "androidx.")
LOCALES = (("en", "us"),)
DETAIL_BATCH = 80
PASS_TARGET = 5
DETAIL_ATTEMPTS = (
    (15, True),
    (40, False),
    (20, False),
)
CHART_PAGES = {
    "GAME_PUZZLE": "https://play.google.com/store/apps/category/GAME_PUZZLE?hl=en&gl=us",
    "GAME": "https://play.google.com/store/games?hl=en&gl=us",
}


def extract_app_ids(html: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for app_id in APP_ID_RE.findall(html):
        if app_id in seen or app_id.startswith(SKIP_PREFIXES):
            continue
        seen.add(app_id)
        found.append(app_id)
    return found


class DetailLimit(RuntimeError):
    pass


class PlayStore:
    def __init__(self, sleeper=time.sleep, app_limit: int = 80, guard: Guard | None = None) -> None:
        self.sleeper = sleeper
        self.app_limit = app_limit
        self.app_calls = 0
        self.guard = guard or Guard()
        self.halted = False

    def _halted(self) -> bool:
        if self.halted or self.guard.active("play"):
            self.halted = True
            return True
        return False

    def _watch(self, exc: Exception) -> bool:
        if limit_kind(exc) is None:
            return False
        self.halted = True
        self.guard.trip("play", "day", "Play bị giới hạn hoặc chặn")
        print(f"Dừng Play: {exc}")
        return True

    def app_details(self, app_id: str, lang: str = "en", country: str = "us") -> dict:
        if self._halted():
            raise DetailLimit("Play đang nghỉ")
        if self.app_calls >= self.app_limit:
            raise DetailLimit("Đã chạm giới hạn lấy chi tiết app")
        if self.app_calls:
            self.sleeper(0.8)
        self.app_calls += 1
        try:
            return fetch_app(app_id, lang=lang, country=country)
        except Exception as exc:
            if self._watch(exc):
                raise DetailLimit("Play đang nghỉ") from exc
            raise

    def search_apps(self, term: str, lang: str, country: str, n_hits: int = 15) -> list[dict]:
        if self._halted():
            return []
        self.sleeper(0.4)
        try:
            return list(fetch_search(term, n_hits=n_hits, lang=lang, country=country))
        except Exception as exc:
            if self._watch(exc):
                return []
            print(f"search lỗi '{term}' {lang}/{country}: {exc}")
            return []

    def similar_ids(self, app_id: str, lang: str, country: str) -> list[str]:
        if self._halted():
            return []
        self.sleeper(0.4)
        url = f"https://play.google.com/store/apps/details?id={app_id}&hl={lang}&gl={country}"
        try:
            html = get(url)
        except Exception as exc:
            if self._watch(exc):
                return []
            print(f"similar lỗi {app_id} {lang}/{country}: {exc}")
            return []
        return [found for found in extract_app_ids(html) if found != app_id]

    def chart_ids(self) -> dict[str, list[str]]:
        pages: dict[str, list[str]] = {}
        for name, url in CHART_PAGES.items():
            if self._halted():
                pages[name] = []
                continue
            self.sleeper(0.4)
            try:
                html = get(url)
            except Exception as exc:
                if self._watch(exc):
                    pages[name] = []
                    continue
                print(f"chart lỗi {name}: {exc}")
                pages[name] = []
                continue
            pages[name] = extract_app_ids(html)
        return pages


def merge_blocklist(blocklist: dict, charts: dict[str, list[str]], seen_on: str) -> dict:
    apps = dict(blocklist.get("apps") or {})
    for source, ids in charts.items():
        for app_id in ids:
            current = apps.get(app_id)
            if current is None:
                apps[app_id] = {"first_seen": seen_on, "sources": [source]}
                continue
            sources = list(current.get("sources") or [])
            if source not in sources:
                sources.append(source)
            current["sources"] = sources
            apps[app_id] = current
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


def collect_partials(
    play: PlayStore,
    anchors: list[str],
    queries: list[str],
    n_hits: int = 15,
    exclude: set[str] | None = None,
    include_similar: bool = True,
) -> list[dict]:
    partials: list[dict] = []
    seen: set[str] = set(exclude or ())

    def add(item: dict) -> None:
        app_id = str(item.get("appId") or "")
        if not app_id or app_id in seen or app_id.startswith(SKIP_PREFIXES):
            return
        seen.add(app_id)
        partials.append(item)

    if include_similar:
        for app_id in anchors[:6]:
            for lang, country in LOCALES:
                for found in play.similar_ids(app_id, lang, country):
                    add({"appId": found})
    for query in queries[:6]:
        for lang, country in LOCALES:
            for hit in play.search_apps(query, lang, country, n_hits=n_hits):
                add(hit)
    return partials


def shortlist(
    play: PlayStore,
    partials: list[dict],
    catalog: dict,
    seeds: list[dict],
    publisher_names: list[str],
    chart_ids: set[str],
    keywords: list[str] | None = None,
    limit: int = 12,
) -> tuple[list[dict], int, list[str], list[str]]:
    passed: list[dict] = []
    scanned = 0
    opened: list[str] = []
    handled: list[str] = []
    skipped = {"duplicate": 0, "chart": 0, "paid": 0, "partial": 0, "detail": 0}
    for partial in partials:
        app_id = str(partial.get("appId") or "")
        title = str(partial.get("title") or "")
        if is_duplicate(app_id, title, catalog, seeds):
            skipped["duplicate"] += 1
            handled.append(app_id)
            continue
        if app_id in chart_ids:
            skipped["chart"] += 1
            handled.append(app_id)
            continue
        if partial.get("free") is False:
            skipped["paid"] += 1
            handled.append(app_id)
            continue
        if _partial_rejected(partial, publisher_names, chart_ids, keywords or []):
            skipped["partial"] += 1
            handled.append(app_id)
            continue
        try:
            detail = play.app_details(app_id, lang="en", country="us")
        except NotFoundError:
            print(f"không thấy {app_id}")
            opened.append(app_id)
            handled.append(app_id)
            continue
        except DetailLimit:
            print("dừng lấy chi tiết vì đã đủ quota")
            break
        except Exception as exc:
            print(f"chi tiết lỗi {app_id}: {exc}")
            skipped["detail"] += 1
            opened.append(app_id)
            handled.append(app_id)
            continue
        scanned += 1
        opened.append(app_id)
        handled.append(app_id)
        detail["appId"] = app_id
        if is_duplicate(app_id, str(detail.get("title") or ""), catalog, seeds):
            skipped["duplicate"] += 1
            continue
        reason = hard_reject(detail, publisher_names, chart_ids, keywords or [])
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        passed.append(detail)
        if len(passed) >= limit:
            break
    print(f"bỏ qua: {skipped}")
    return passed, scanned, opened, handled


def gather_passed(
    play: PlayStore,
    anchors: list[str],
    queries: list[str],
    catalog: dict,
    seeds: list[dict],
    publisher_names: list[str],
    chart_ids: set[str],
    already_seen: set[str],
    match_titles: list[str],
    keywords: list[str] | None = None,
) -> tuple[list[dict], int, list[str]]:
    play.app_limit = DETAIL_BATCH
    exclude = set(already_seen)
    passed: list[dict] = []
    scanned = 0
    opened: list[str] = []
    cleaned = [text for text in (strip_ignored(query, keywords or []) for query in queries) if text]
    titles = [text for text in (strip_ignored(title, keywords or []) for title in match_titles) if text]
    query_sets = (cleaned, cleaned, titles)
    for index, ((n_hits, include_similar), batch_queries) in enumerate(
        zip(DETAIL_ATTEMPTS, query_sets), start=1
    ):
        if len(passed) >= PASS_TARGET or play.halted:
            break
        play.app_calls = 0
        partials = collect_partials(
            play,
            anchors,
            batch_queries,
            n_hits=n_hits,
            exclude=exclude,
            include_similar=include_similar,
        )
        if not partials:
            print(f"đợt {index}: hết ứng viên mới")
            continue
        batch_passed, batch_scanned, batch_opened, handled = shortlist(
            play,
            partials,
            catalog,
            seeds,
            publisher_names,
            chart_ids,
            keywords or [],
            limit=DETAIL_BATCH,
        )
        passed.extend(batch_passed)
        scanned += batch_scanned
        opened.extend(batch_opened)
        exclude.update(handled)
        print(f"đợt {index}: ứng viên {len(partials)}, đã lấy chi tiết {batch_scanned}, qua lọc {len(batch_passed)}")
    return passed, scanned, opened


def _partial_rejected(
    partial: dict,
    publisher_names: list[str],
    chart_ids: set[str],
    keywords: list[str],
) -> bool:
    title = str(partial.get("title") or "")
    if title and title_ignored(title, keywords):
        return True
    developer = str(partial.get("developer") or "")
    if developer and publisher_blocked(developer, publisher_names):
        return True
    installs = partial.get("minInstalls")
    if not isinstance(installs, int):
        raw = partial.get("installs")
        installs = parse_installs(raw) if raw else None
    if installs is not None and not in_install_band(installs):
        return True
    if (partial.get("genreId") or partial.get("genre")) and not is_puzzle(partial):
        return True
    app_id = str(partial.get("appId") or "")
    return bool(app_id and app_id in chart_ids)
