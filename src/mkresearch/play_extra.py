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
from mkresearch.filters import in_install_band, is_puzzle, parse_installs, title_ignored
from mkresearch.discover import remember_queries as remember_query_state
from mkresearch.discover import remember_seen as remember_seen_ids
from mkresearch.store import Store
from mkresearch.telegram import Telegram, first_screenshot, format_report, play_url

SEARCH_SCRIPT = Path("tools/play_extra/search/search.mjs")
CLUSTER_BIN = Path("tools/play_extra/category/category")
CLUSTER_DIR = Path("tools/play_extra/category")
OPEN_LIMIT = 120
PASS_LIMIT = 5
SOURCE_OPEN = OPEN_LIMIT // 2
MR_ADEX = "MrAdex77"
KRYUCHENKO = "kryuchenko"


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
        timeout=180,
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
        timeout=420,
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
) -> tuple[list[dict], list[str], list[str]]:
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
    seen_partial = set(exclude)
    cluster_partials, _cluster_ids, _cluster_dropped = _take_hits(
        _cluster_hits(cluster_fn, chosen), seen_partial, keywords
    )
    search_partials: list[dict] = []
    dropped: list[str] = []
    for query in chosen:
        try:
            hits = list(search_fn(query) or [])
        except Exception as exc:
            print(f"search riêng lỗi '{query}': {exc}")
            continue
        kept, ids, skipped = _take_hits(hits, seen_partial, keywords)
        search_partials.extend(kept)
        dropped.extend(skipped)
        returned[query] = ids
    sources = [
        _scan_source(play, KRYUCHENKO, cluster_partials, catalog, seeds, publishers, chart_ids, keywords),
        _scan_source(play, MR_ADEX, search_partials, catalog, seeds, publishers, chart_ids, keywords),
    ]
    handled = [app_id for source in sources for app_id in source["handled"]]
    handled.extend(dropped)
    retired = _retire_queries(returned, set(), set(handled), exclude)
    kept = [item for item in leftover if str(item).strip()]
    return sources, retired, kept


def format_extra_report(app: dict, package: str) -> str:
    return f"{package}\n" + "Play riêng\n" + format_report(app, {"near_seed": package})


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
    sources, retired, kept = search_extra(
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
    opened = [app_id for source in sources for app_id in source["opened"]]
    extra.save_seen(remember_seen_ids(extra.load_seen(), opened, datetime.now(timezone.utc).date().isoformat()))
    extra.save_queries(remember_query_state(saved, retired, kept))
    extra_catalog = extra.load_catalog()
    sent = 0
    for source in sources:
        package = str(source["name"])
        passed = list(source["passed"])
        for app in passed:
            print(
                f"{package} qua lọc: {app.get('title')} | {app.get('appId')} | "
                f"{app.get('developer')} | {app.get('installs')}"
            )
        package_sent = 0
        for app in passed:
            try:
                message_id = telegram.send_report(format_extra_report(app, package), first_screenshot(app))
            except Exception as exc:
                print(f"Không gửi được {app.get('appId')}: {exc}")
                continue
            _remember(extra_catalog, app, message_id)
            extra.save_catalog(extra_catalog)
            package_sent += 1
            sent += 1
        if package_sent == 0:
            scanned = int(source["scanned"])
            telegram.send(
                f"{package}: Không có game mới. Đã xem {scanned} app, {len(passed)} game qua bộ lọc."
            )
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


def _cluster_hits(cluster_fn, terms: list[str]) -> list[dict]:
    try:
        return list(cluster_fn(terms) or [])
    except Exception as exc:
        print(f"cluster lỗi: {exc}")
        return []


def _take_hits(
    hits: list[dict],
    seen_partial: set[str],
    keywords: list[str],
) -> tuple[list[dict], list[str], list[str]]:
    kept: list[dict] = []
    ids: list[str] = []
    dropped: list[str] = []
    for hit in hits:
        app_id = str(hit.get("appId") or "")
        if not app_id or app_id in ids:
            continue
        ids.append(app_id)
        if app_id in seen_partial:
            continue
        seen_partial.add(app_id)
        if not _card_can_pass(hit, keywords):
            dropped.append(app_id)
            continue
        kept.append(hit)
    return kept, ids, dropped


def _card_can_pass(hit: dict, keywords: list[str]) -> bool:
    if hit.get("free") is False:
        return False
    text = " ".join(str(hit.get(key) or "") for key in ("title", "summary"))
    if text.strip() and title_ignored(text, keywords):
        return False
    installs = hit.get("minInstalls")
    if not isinstance(installs, int):
        raw = hit.get("installs")
        installs = parse_installs(raw) if raw else None
    if installs is not None and not in_install_band(installs):
        return False
    if (hit.get("genreId") or hit.get("genre")) and not is_puzzle(hit):
        return False
    return True


def _scan_source(play, name, partials, catalog, seeds, publishers, chart_ids, keywords) -> dict:
    play.app_calls = 0
    play.app_limit = SOURCE_OPEN
    passed, scanned, opened, handled = shortlist(
        play,
        partials,
        catalog,
        seeds,
        publishers,
        chart_ids,
        keywords,
        limit=PASS_LIMIT,
        label=name,
        candidates=len(partials),
    )
    return {
        "name": name,
        "passed": passed,
        "scanned": scanned,
        "opened": opened,
        "handled": handled,
    }


def _cluster_command(terms: list[str]) -> list[str] | None:
    del terms
    args = ["-throttle", "400ms"]
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
