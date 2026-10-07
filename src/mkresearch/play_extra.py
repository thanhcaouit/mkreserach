from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mkresearch.cooldown import Guard, LimitReached
from mkresearch.dedupe import normalize_title
from mkresearch.discover import (
    QUERY_TARGET,
    PlayStore,
    _fill_queries,
    _retire_queries,
    blocked_ids,
    normalize_query,
    seen_ids,
    shortlist,
    suggestion_on_topic,
)
from mkresearch.discover import remember_queries as remember_query_state
from mkresearch.discover import remember_seen as remember_seen_ids
from mkresearch.store import Store
from mkresearch.telegram import Telegram, first_screenshot, format_report, play_url

SEARCH_SCRIPT = Path("tools/play_extra/search/search.mjs")
CLUSTER_BIN = Path("tools/play_extra/category/category")
CLUSTER_DIR = Path("tools/play_extra/category")
OPEN_LIMIT = 40
PASS_LIMIT = 5


class ExtraStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "play_extra"

    def load_seen(self) -> dict:
        return _read(self.root / "seen.json", {"apps": {}})

    def save_seen(self, seen: dict) -> None:
        _write(self.root / "seen.json", seen)

    def load_catalog(self) -> dict:
        return _read(self.root / "catalog.json", {"apps": {}})

    def save_catalog(self, catalog: dict) -> None:
        _write(self.root / "catalog.json", catalog)

    def load_queries(self) -> dict:
        return _read(self.root / "queries.json", {"exhausted": [], "pending": []})

    def save_queries(self, queries: dict) -> None:
        _write(self.root / "queries.json", queries)


def play_data_ids(store: Store) -> set[str]:
    ids = seen_ids(store.load_seen())
    ids.update(str(app_id) for app_id in (store.load_catalog().get("apps") or {}))
    ids.update(blocked_ids(store.load_blocklist()))
    return ids


def node_search(term: str) -> list[dict]:
    if not SEARCH_SCRIPT.exists():
        print(f"Thiếu {SEARCH_SCRIPT}")
        return []
    proc = subprocess.run(
        ["node", str(SEARCH_SCRIPT), term],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        print(f"search riêng lỗi '{term}': {proc.stderr[-400:]}")
        return []
    return _json_rows(proc.stdout, term)


def cluster_search(terms: list[str]) -> list[dict]:
    command = _cluster_command(terms)
    if not command:
        return []
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if proc.returncode != 0:
        print(f"cluster lỗi: {proc.stderr[-400:]}")
        return []
    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def search_extra(
    play: PlayStore,
    queries: list[str],
    search_words: list[str],
    keywords: list[str],
    exhausted: list[str],
    pending: list[str],
    liked: str | None,
    exclude: set[str],
    catalog: dict,
    seeds: list[dict],
    publishers: list[str],
    chart_ids: set[str],
    search_fn,
    cluster_fn,
    rng: random.Random | None = None,
) -> tuple[list[dict], int, list[str], list[str], list[str]]:
    picker = rng or random.Random()
    topic_pending = [item for item in pending if suggestion_on_topic(item)]
    dead = {normalize_query(item) for item in exhausted if str(item).strip()}
    chosen, _used_seeds, leftover = _fill_queries(
        play,
        [*topic_pending, *queries],
        search_words,
        keywords,
        dead,
        picker,
        liked,
        QUERY_TARGET,
    )
    returned: dict[str, list[str]] = {}
    partials: list[dict] = []
    seen_partial = set(exclude)
    for query in chosen:
        try:
            hits = list(search_fn(query) or [])
        except Exception as exc:
            print(f"search riêng lỗi '{query}': {exc}")
            continue
        ids: list[str] = []
        for hit in hits:
            app_id = str(hit.get("appId") or "")
            if not app_id or app_id in ids:
                continue
            ids.append(app_id)
            if app_id in seen_partial:
                continue
            seen_partial.add(app_id)
            partials.append(hit)
        returned[query] = ids
    try:
        for hit in list(cluster_fn(chosen) or []):
            app_id = str(hit.get("appId") or "")
            if not app_id or app_id in seen_partial:
                continue
            seen_partial.add(app_id)
            partials.append(hit)
    except Exception as exc:
        print(f"cluster lỗi: {exc}")
    play.app_limit = OPEN_LIMIT
    passed, scanned, opened, handled = shortlist(
        play,
        partials,
        catalog,
        seeds,
        publishers,
        chart_ids,
        keywords,
        limit=PASS_LIMIT,
    )
    retired = _retire_queries(returned, set(), set(handled), exclude)
    kept = [item for item in leftover if str(item).strip()]
    print(f"Play riêng: đã xem {scanned}, qua lọc {len(passed)}")
    return passed, scanned, opened, retired, kept


def format_extra_report(app: dict) -> str:
    return "Play riêng\n" + format_report(app, {"near_seed": "Play riêng"})


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(".env"))
    _load_dotenv(Path(".env.local"))
    if argv:
        print("Dùng: python -m mkresearch.play_extra", file=sys.stderr)
        return 2
    store = Store(Path(os.environ.get("DATA_DIR", "data")))
    extra = ExtraStore(store.root)
    guard = Guard(store.load_cooldown())
    telegram = Telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    if not telegram.configured():
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    play = PlayStore(guard=guard)
    try:
        code = run_extra(store, extra, play, telegram)
        store.save_cooldown(guard.state)
        return code
    except LimitReached as exc:
        store.save_cooldown(guard.state)
        print(f"Nghỉ gọi dịch vụ: {exc}")
        return 0
    except Exception:
        store.save_cooldown(guard.state)
        raise


def run_extra(
    store: Store,
    extra: ExtraStore,
    play: PlayStore,
    telegram: Telegram,
    search_fn=node_search,
    cluster_fn=cluster_search,
) -> int:
    if play.halted or play.guard.active("play"):
        print("Đang trong thời gian nghỉ, không search riêng.")
        return 0
    profile = store.load_profile()
    keywords = store.load_ignore_keywords()
    saved = extra.load_queries()
    mechanics = [str(item).strip() for item in (profile.get("liked_mechanics") or []) if str(item).strip()]
    queries = [str(item) for item in (profile.get("search_queries") or []) if str(item).strip()]
    pending = [str(item) for item in (saved.get("pending") or []) if str(item).strip()]
    catalog = {"apps": {}}
    passed, _scanned, opened, retired, kept = search_extra(
        play,
        queries,
        store.load_search_words(),
        keywords,
        [str(item) for item in (saved.get("exhausted") or [])],
        pending,
        mechanics[0] if mechanics else None,
        play_data_ids(store) | seen_ids(extra.load_seen()),
        catalog,
        store.load_seeds(),
        store.load_publishers(),
        blocked_ids(store.load_blocklist()),
        search_fn,
        cluster_fn,
    )
    extra.save_seen(remember_seen_ids(extra.load_seen(), opened, datetime.now(timezone.utc).date().isoformat()))
    extra.save_queries(remember_query_state(saved, retired, kept))
    extra_catalog = extra.load_catalog()
    sent = 0
    for app in passed:
        try:
            message_id = telegram.send_report(format_extra_report(app), first_screenshot(app))
        except Exception as exc:
            print(f"Không gửi được {app.get('appId')}: {exc}")
            continue
        _remember(extra_catalog, app, message_id)
        extra.save_catalog(extra_catalog)
        sent += 1
    print(f"Đã gửi {sent} game Play riêng")
    return 0


def _remember(catalog: dict, app: dict, message_id: int) -> None:
    app_id = str(app.get("appId") or "")
    title = str(app.get("title") or "")
    catalog.setdefault("apps", {})[app_id] = {
        "app_id": app_id,
        "title": title,
        "title_norm": normalize_title(title),
        "developer": app.get("developer"),
        "installs": app.get("installs"),
        "min_installs": app.get("minInstalls"),
        "score": app.get("score"),
        "url": play_url(app_id),
        "message_id": message_id,
        "suggested_at": datetime.now(timezone.utc).isoformat(),
    }


def _cluster_command(terms: list[str]) -> list[str] | None:
    args = ["-max", "300", "-throttle", "400ms", *terms]
    if CLUSTER_BIN.exists():
        return [str(CLUSTER_BIN), *args]
    if (CLUSTER_DIR / "main.go").exists():
        return ["go", "run", str(CLUSTER_DIR), *args]
    print("Thiếu công cụ cluster")
    return None


def _json_rows(raw: str, term: str) -> list[dict]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        print(f"search riêng JSON lỗi '{term}': {exc}")
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _read(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if __name__ == "__main__":
    raise SystemExit(main())
