from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import urlencode

from google_play_scraper import app as fetch_app
from google_play_scraper import search as fetch_search
from google_play_scraper.exceptions import NotFoundError
from google_play_scraper.utils.request import get, post

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
QUERY_TARGET = 6
SUGGEST_SEEDS = 3
SUGGEST_URL = "https://play.google.com/_/PlayStoreUi/data/batchexecute?rpcids=IJ4APc&hl=en&gl=us&rt=c"
INTENT_FILLER = {"puzzle", "level", "levels", "game", "games"}
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

    def suggestions(self, term: str) -> list[str]:
        if self._halted() or not str(term).strip():
            return []
        self.sleeper(0.4)
        try:
            raw = post(SUGGEST_URL, _suggest_body(term), {"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            if self._watch(exc):
                return []
            print(f"gợi ý lỗi '{term}': {exc}")
            return []
        return parse_play_suggestions(raw)

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


def normalize_query(text: str) -> str:
    return " ".join(text.casefold().split())


def remember_queries(saved: dict, queries: list[str], pending: list[str] | None = None) -> dict:
    existing = [str(item).strip() for item in (saved.get("exhausted") or []) if str(item).strip()]
    seen = {normalize_query(item) for item in existing}
    merged = list(existing)
    for query in queries:
        token = str(query).strip()
        key = normalize_query(token)
        if not token or key in seen:
            continue
        merged.append(token)
        seen.add(key)
    if pending is None:
        kept = [str(item).strip() for item in (saved.get("pending") or []) if str(item).strip()]
    else:
        kept = [str(item).strip() for item in pending if str(item).strip()]
    kept = [item for item in kept if normalize_query(item) not in seen]
    return {"exhausted": merged, "pending": kept}


def choose_random_query(
    words: list[str],
    keywords: list[str],
    exhausted: set[str],
    rng: random.Random,
) -> str | None:
    pool: list[str] = []
    for raw in words:
        word = str(raw).strip()
        if not word or title_ignored(word, keywords):
            continue
        query = strip_ignored(f"{word} puzzle levels", keywords)
        key = normalize_query(query)
        if not query or key in exhausted or key in {normalize_query(item) for item in pool}:
            continue
        pool.append(query)
    if not pool:
        return None
    return rng.choice(pool)


def query_intent(text: str) -> tuple[str, ...]:
    tokens = normalize_query(text).split()
    return tuple(token for token in tokens if token not in INTENT_FILLER)


def parse_play_suggestions(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        chunk = line.strip()
        if not chunk.startswith("["):
            continue
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        for name in _suggestion_names(payload):
            key = normalize_query(name)
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(name.strip())
    return found


def _suggest_body(term: str) -> bytes:
    inner = json.dumps([[None, [term], [10], [2], 4]])
    envelope = json.dumps([[["IJ4APc", inner, None, "generic"]]])
    return urlencode({"f.req": envelope}).encode()


def _suggestion_names(node: object) -> list[str]:
    if isinstance(node, str):
        if not node.startswith("["):
            return []
        try:
            return _suggestion_names(json.loads(node))
        except json.JSONDecodeError:
            return []
    if not isinstance(node, list):
        return []
    found: list[str] = []
    for child in node:
        found.extend(_suggestion_names(child))
    if found:
        return found
    if node and isinstance(node[0], str) and _contains_search(node[1:]):
        return [node[0]]
    return []


def _contains_search(node: object) -> bool:
    if isinstance(node, str):
        return "/store/search" in node
    if isinstance(node, list):
        return any(_contains_search(child) for child in node)
    return False


def _accept_query(text: str, keywords: list[str], blocked: set[str], chosen: list[str], drop_ignored: bool) -> str | None:
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if drop_ignored:
        if title_ignored(cleaned, keywords):
            return None
    else:
        cleaned = strip_ignored(cleaned, keywords)
    key = normalize_query(cleaned)
    if not cleaned or not key or key in blocked:
        return None
    intent = query_intent(cleaned)
    if any(query_intent(item) == intent for item in chosen):
        return None
    return cleaned


def suggestion_seeds(
    words: list[str],
    keywords: list[str],
    blocked: set[str],
    rng: random.Random,
    liked: str | None = None,
    limit: int = SUGGEST_SEEDS,
) -> list[str]:
    pool: list[str] = []
    for raw in words:
        word = str(raw).strip()
        if not word or title_ignored(word, keywords):
            continue
        fallback = normalize_query(strip_ignored(f"{word} puzzle levels", keywords))
        if normalize_query(word) in blocked or fallback in blocked:
            continue
        pool.append(word)
    rng.shuffle(pool)
    chosen: list[str] = []
    token = str(liked or "").strip()
    if token and not title_ignored(token, keywords) and normalize_query(token) not in blocked:
        chosen.append(token)
    extra = 0
    seen = {normalize_query(item) for item in chosen}
    for word in pool:
        if extra >= limit:
            break
        key = normalize_query(word)
        if key in seen:
            continue
        chosen.append(word)
        seen.add(key)
        extra += 1
    return chosen


def suggestion_on_topic(text: str) -> bool:
    folded = " ".join(str(text).casefold().split())
    return any(word in folded for word in ("puzzle", "maze", "level"))


def collect_suggestions(play: PlayStore, seeds: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    method = getattr(play, "suggestions", None)
    if method is None:
        return []
    for seed in seeds:
        text = str(seed).strip()
        if not text:
            continue
        for term in (f"{text} puzzle", f"{text} puzzle "):
            try:
                names = list(method(term) or [])
            except Exception as exc:
                print(f"gợi ý lỗi '{term}': {exc}")
                names = []
            for name in names:
                cleaned = str(name).strip()
                if not suggestion_on_topic(cleaned):
                    continue
                key = normalize_query(cleaned)
                if not key or key in seen:
                    continue
                seen.add(key)
                found.append(cleaned)
    return found


def _fallback_query(
    words: list[str],
    keywords: list[str],
    blocked: set[str],
    chosen: list[str],
    rng: random.Random,
) -> str | None:
    skipped = set(blocked)
    while True:
        extra = choose_random_query(words, keywords, skipped, rng)
        if not extra:
            return None
        if any(query_intent(item) == query_intent(extra) for item in chosen):
            skipped.add(normalize_query(extra))
            continue
        return extra


def _fill_queries(
    play: PlayStore,
    sources: list[str],
    words: list[str],
    keywords: list[str],
    blocked: set[str],
    rng: random.Random,
    liked: str | None,
    limit: int,
) -> tuple[list[str], list[str], list[str]]:
    chosen: list[str] = []
    used_blocked = set(blocked)
    for text in sources:
        accepted = _accept_query(text, keywords, used_blocked, chosen, False)
        if not accepted:
            continue
        chosen.append(accepted)
        used_blocked.add(normalize_query(accepted))
        if len(chosen) >= limit:
            return chosen, [], []
    seeds = suggestion_seeds(words, keywords, used_blocked, rng, liked)
    suggestions = collect_suggestions(play, seeds)
    leftover: list[str] = []
    for text in suggestions:
        if len(chosen) >= limit:
            leftover.append(text)
            continue
        accepted = _accept_query(text, keywords, used_blocked, chosen, True)
        if not accepted:
            continue
        chosen.append(accepted)
        used_blocked.add(normalize_query(accepted))
    while len(chosen) < limit:
        extra = _fallback_query(words, keywords, used_blocked, chosen, rng)
        if not extra:
            break
        chosen.append(extra)
        used_blocked.add(normalize_query(extra))
    return chosen, seeds, leftover


def _retire_queries(
    returned: dict[str, list[str]],
    productive: set[str],
    handled: set[str],
    excluded_before: set[str],
) -> list[str]:
    retired: list[str] = []
    for query, ids in returned.items():
        key = normalize_query(query)
        if not key or key in productive:
            continue
        if any(app_id not in excluded_before and app_id not in handled for app_id in ids):
            continue
        retired.append(query)
    return retired


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
) -> tuple[list[dict], dict[str, list[str]]]:
    partials: list[dict] = []
    seen: set[str] = set(exclude or ())
    returned: dict[str, list[str]] = {}

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
    for query in queries:
        found: list[str] = []
        for lang, country in LOCALES:
            for hit in play.search_apps(query, lang, country, n_hits=n_hits):
                app_id = str(hit.get("appId") or "")
                if not app_id or app_id.startswith(SKIP_PREFIXES) or app_id in found:
                    continue
                found.append(app_id)
                add(hit)
        returned[query] = found
    return partials, returned


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
    exhausted: list[str] | None = None,
    search_words: list[str] | None = None,
    rng: random.Random | None = None,
    pending: list[str] | None = None,
    liked: str | None = None,
) -> tuple[list[dict], int, list[str], list[str], list[str]]:
    play.app_limit = DETAIL_BATCH
    exclude = set(already_seen)
    passed: list[dict] = []
    scanned = 0
    opened: list[str] = []
    words = list(search_words or [])
    keyword_list = list(keywords or [])
    picker = rng or random.Random()
    dead = {normalize_query(item) for item in (exhausted or [])}
    profile_keys: set[str] = set()
    profile_raw = {normalize_query(item) for item in queries}
    sources: list[str] = []
    topic_pending = [
        text
        for text in (pending or [])
        if suggestion_on_topic(text) or normalize_query(text) in profile_raw
    ]
    for text in [*topic_pending, *queries]:
        accepted = _accept_query(text, keyword_list, dead, sources, False)
        if not accepted:
            continue
        if normalize_query(text) not in {normalize_query(item) for item in topic_pending}:
            profile_keys.add(normalize_query(accepted))
        sources.append(accepted)
        if len(sources) >= QUERY_TARGET:
            break
    chosen, used_seeds, leftover = _fill_queries(
        play, sources, words, keyword_list, dead, picker, liked, QUERY_TARGET
    )
    for query in chosen:
        if normalize_query(query) in {normalize_query(item) for item in queries}:
            profile_keys.add(normalize_query(query))
    titles = [text for text in (strip_ignored(title, keyword_list) for title in match_titles) if text]
    productive: set[str] = set()
    retired: list[str] = []
    searched: list[str] = []

    def run_batch(index: int, n_hits: int, include_similar: bool, batch_queries: list[str], retire: bool) -> None:
        nonlocal scanned
        if not batch_queries and not include_similar:
            return
        searched.extend(batch_queries)
        play.app_calls = 0
        partials, returned = collect_partials(
            play,
            anchors,
            batch_queries,
            n_hits=n_hits,
            exclude=exclude,
            include_similar=include_similar,
        )
        if not partials:
            print(f"đợt {index}: hết ứng viên mới")
            if retire and not play.halted:
                retired.extend(_retire_queries(returned, productive, set(), exclude))
            return
        batch_passed, batch_scanned, batch_opened, handled = shortlist(
            play,
            partials,
            catalog,
            seeds,
            publisher_names,
            chart_ids,
            keyword_list,
            limit=DETAIL_BATCH,
        )
        opened_now = set(batch_opened)
        for query, ids in returned.items():
            if any(app_id in opened_now for app_id in ids):
                productive.add(normalize_query(query))
        if retire and not play.halted:
            retired.extend(_retire_queries(returned, productive, set(handled), exclude))
        passed.extend(batch_passed)
        scanned += batch_scanned
        opened.extend(batch_opened)
        exclude.update(handled)
        print(f"đợt {index}: ứng viên {len(partials)}, đã lấy chi tiết {batch_scanned}, qua lọc {len(batch_passed)}")

    for index, (n_hits, include_similar) in enumerate(((15, True), (40, False)), start=1):
        if len(passed) >= PASS_TARGET or play.halted:
            break
        run_batch(index, n_hits, include_similar, chosen, retire=index == 2)
    unused: list[str] = []
    if len(passed) < PASS_TARGET and not play.halted:
        blocked = dead | {normalize_query(item) for item in chosen} | {normalize_query(item) for item in used_seeds}
        third, _seeds, unused = _fill_queries(
            play, leftover, words, keyword_list, blocked, picker, None, QUERY_TARGET
        )
        if third:
            run_batch(3, 40, False, third, retire=True)
        else:
            run_batch(3, 20, False, titles, retire=True)
    retired_keys = {normalize_query(item) for item in retired}
    searched_keys = {normalize_query(item) for item in searched}
    kept: list[str] = []
    seen_pending: set[str] = set()
    for query in [*topic_pending, *unused]:
        key = normalize_query(query)
        if not key or key in dead or key in retired_keys or key in searched_keys or key in seen_pending:
            continue
        if not suggestion_on_topic(query):
            continue
        kept.append(query)
        seen_pending.add(key)
    for query in searched:
        key = normalize_query(query)
        if not key or key in retired_keys or key in profile_keys or key in seen_pending:
            continue
        if not suggestion_on_topic(query):
            continue
        kept.append(query)
        seen_pending.add(key)
    if retired:
        print(f"bỏ câu tìm: {retired}")
    return passed, scanned, opened, retired, kept


def _partial_rejected(
    partial: dict,
    publisher_names: list[str],
    chart_ids: set[str],
    keywords: list[str],
) -> bool:
    text = " ".join(str(partial.get(key) or "") for key in ("title", "summary"))
    if text.strip() and title_ignored(text, keywords):
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
