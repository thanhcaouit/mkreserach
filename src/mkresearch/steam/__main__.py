from __future__ import annotations

import os
import random
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from mkresearch.cooldown import Guard, LimitReached
from mkresearch.dedupe import normalize_title
from mkresearch.discover import _retire_queries, choose_random_query, normalize_query, remember_queries
from mkresearch.llm import LlmClient
from mkresearch.steam.client import SteamClient
from mkresearch.steam.discover import (
    DETAIL_CAP,
    PASS_TARGET,
    DetailLimit,
    blocked_ids,
    merge_blocklist,
    recent_match_titles,
    remember_seen,
    seen_ids,
)
from mkresearch.steam.filters import hard_reject
from mkresearch.steam.judge import judge
from mkresearch.steam.learn import update_profile
from mkresearch.steam.report import first_screenshot, format_report, steam_url
from mkresearch.steam.store import SteamData, already_sent
from mkresearch.telegram import Telegram, collect_ratings, message_index


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(".env"))
    _load_dotenv(Path(".env.local"))
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"research", "smoke"}:
        print("Dùng: python -m mkresearch.steam research|smoke", file=sys.stderr)
        return 2
    mode = args[0]
    data = SteamData(Path(os.environ.get("STEAM_DATA_DIR", "data/steam")))
    guard = Guard(data.load_cooldown())
    telegram = Telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    llm = LlmClient(guard=guard)
    client = SteamClient(guard=guard)
    try:
        if mode == "smoke":
            code = run_smoke(data, client, llm, telegram)
        else:
            code = run_research(data, client, llm, telegram)
        _notify_pending(guard, telegram)
        data.save_cooldown(guard.state)
        return code
    except LimitReached as exc:
        _notify_pending(guard, telegram)
        data.save_cooldown(guard.state)
        print(f"Nghỉ gọi dịch vụ: {exc}")
        return 0
    except Exception as exc:
        data.save_cooldown(guard.state)
        print(f"Lỗi: {exc}", file=sys.stderr)
        traceback.print_exc()
        if mode == "research" and telegram.configured():
            try:
                telegram.send(f"Steam research lỗi: {exc}")
            except Exception as send_exc:
                print(f"Không gửi được Telegram: {send_exc}", file=sys.stderr)
        return 1


def run_research(data: SteamData, client: SteamClient, llm: LlmClient, telegram: Telegram) -> int:
    if not telegram.configured():
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    if not llm.available():
        raise RuntimeError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
    seeds = data.load_seeds()
    if not seeds:
        raise RuntimeError("data/steam/seeds.yaml chưa có game mẫu")
    if client.halted or _llm_paused(llm):
        print("Đang trong thời gian nghỉ, không gọi Steam và AI.")
        _pull_ratings(data, telegram)
        return 0
    ratings, changed = _pull_ratings(data, telegram)
    profile = data.load_profile()
    if changed and not _llm_paused(llm):
        try:
            profile = update_profile(llm, seeds, ratings["items"], profile, _exhausted_queries(data))
            data.save_profile(profile)
        except LimitReached:
            raise
        except Exception as exc:
            print(f"Giữ hồ sơ Steam cũ vì không cập nhật được: {exc}")
            profile = data.load_profile()

    charts = client.chart_ids()
    blocklist = merge_blocklist(data.load_blocklist(), charts, datetime.now(timezone.utc).date().isoformat())
    data.save_blocklist(blocklist)
    if client.halted:
        print("Steam bị chặn, dừng lượt này.")
        return 0
    chart_ids = blocked_ids(blocklist)
    catalog = data.load_catalog()
    publishers = data.load_publishers()
    play_catalog, play_seeds = data.load_play_reference()
    queries = [str(item) for item in profile.get("search_queries") or []]
    seen = data.load_seen()
    saved_queries = data.load_queries()
    passed, scanned, opened, retired = gather_passed(
        client,
        queries,
        catalog,
        seeds,
        publishers,
        chart_ids,
        play_catalog,
        play_seeds,
        seen_ids(seen),
        recent_match_titles(catalog),
        data.load_ignore_tags(),
        _exhausted_queries(data),
        data.load_search_words(),
    )
    data.save_seen(remember_seen(seen, opened, datetime.now(timezone.utc).date().isoformat()))
    data.save_queries(remember_queries(saved_queries, retired))
    if client.halted and not passed:
        print("Steam bị chặn, dừng lượt này.")
        return 0
    print(f"Đã lấy chi tiết {scanned}, qua lọc {len(passed)}")
    for app in passed:
        print(f"qua lọc: {app.get('title')} | {app.get('appId')} | {app.get('developer')} | {app.get('reviews')}")
    picks = judge(llm, passed, seeds, profile, limit=len(passed) or 5)
    by_id = {str(app.get("appId")): app for app in passed}
    sent = 0
    for pick in picks:
        app = by_id.get(pick["app_id"])
        if app is None:
            continue
        try:
            message_id = telegram.send_report(format_report(app, pick), first_screenshot(app))
        except Exception as exc:
            print(f"Không gửi được {pick['app_id']}: {exc}")
            continue
        _remember(catalog, app, message_id)
        data.save_catalog(catalog)
        sent += 1
    if sent == 0:
        telegram.send(f"Không có game Steam mới. Đã xem {scanned} app, {len(passed)} game qua bộ lọc.")
    print(f"Đã gửi {sent} game Steam")
    return 0


def run_smoke(data: SteamData, client: SteamClient, llm: LlmClient, telegram: Telegram) -> int:
    if not telegram.configured():
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    if not llm.available():
        raise RuntimeError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
    catalog_path = data.root / "catalog.json"
    before = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
    seeds = {str(seed["app_id"]): seed for seed in data.load_seeds()}
    missing = {"357300", "353540"} - set(seeds)
    if missing:
        print("Smoke thiếu seed: " + ", ".join(sorted(missing)))
        return 1
    snakebird = client.app_details("357300")
    sausage = client.app_details("353540")
    failures: list[str] = []
    if "snakebird" not in str(snakebird.get("title") or "").casefold():
        failures.append(f"title Snakebird là {snakebird.get('title')}")
    if "sausage" not in str(sausage.get("title") or "").casefold():
        failures.append(f"title Stephen's Sausage Roll là {sausage.get('title')}")
    if failures:
        print("Smoke thất bại: " + "; ".join(failures))
        return 1
    reply = llm.ping()
    if "ok" not in reply.casefold():
        print(f"Smoke AI không trả ok: {reply}")
        return 1
    telegram.send(
        "steam smoke ok\n"
        + steam_url("357300")
        + "\n"
        + steam_url("353540")
    )
    after = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
    if after != before:
        print("Smoke đã ghi catalog")
        return 1
    print(f"Steam smoke xong. Model {llm.last_model}")
    return 0


def shortlist(
    client: SteamClient,
    app_ids: list[str],
    catalog: dict,
    seeds: list[dict],
    publishers: list[str],
    chart_ids: set[str],
    play_catalog: dict,
    play_seeds: list[dict],
    ignore_tags: list[str] | None = None,
    limit: int = 12,
) -> tuple[list[dict], int, list[str], list[str]]:
    passed: list[dict] = []
    scanned = 0
    opened: list[str] = []
    handled: list[str] = []
    skips: dict[str, int] = {}
    for app_id in app_ids:
        if len(passed) >= limit:
            break
        if client.halted:
            break
        if already_sent(app_id, "", catalog, seeds, play_catalog, play_seeds):
            skips["duplicate"] = skips.get("duplicate", 0) + 1
            handled.append(app_id)
            continue
        try:
            app = client.app_details(app_id)
        except DetailLimit:
            break
        scanned += 1
        opened.append(app_id)
        handled.append(app_id)
        if already_sent(app_id, str(app.get("title") or ""), catalog, seeds, play_catalog, play_seeds):
            skips["duplicate"] = skips.get("duplicate", 0) + 1
            continue
        reason = hard_reject(app, publishers, chart_ids, ignore_tags)
        if reason:
            skips[reason] = skips.get(reason, 0) + 1
            continue
        passed.append(app)
    if skips:
        print("Bỏ qua: " + ", ".join(f"{key} {count}" for key, count in sorted(skips.items())))
    return passed, scanned, opened, handled


def gather_passed(
    client: SteamClient,
    queries: list[str],
    catalog: dict,
    seeds: list[dict],
    publishers: list[str],
    chart_ids: set[str],
    play_catalog: dict,
    play_seeds: list[dict],
    already_seen: set[str],
    match_titles: list[str],
    ignore_tags: list[str] | None = None,
    exhausted: list[str] | None = None,
    search_words: list[str] | None = None,
    rng: random.Random | None = None,
) -> tuple[list[dict], int, list[str], list[str]]:
    exclude = set(already_seen)
    passed: list[dict] = []
    scanned = 0
    opened: list[str] = []
    dead = {normalize_query(item) for item in (exhausted or [])}
    cleaned: list[str] = []
    seen_queries: set[str] = set()
    for query in queries:
        text = " ".join(str(query).split())
        key = normalize_query(text)
        if not text or key in dead or key in seen_queries:
            continue
        cleaned.append(text)
        seen_queries.add(key)
    extra = choose_random_query(search_words or [], [], dead | seen_queries, rng or random.Random())
    if extra:
        cleaned.append(extra)
    titles = [" ".join(title.split()) for title in match_titles if str(title).strip()]
    query_sets = (cleaned, cleaned, titles)
    productive: set[str] = set()
    retired: list[str] = []
    for index, batch_queries in enumerate(query_sets, start=1):
        if len(passed) >= PASS_TARGET or client.halted:
            break
        client.begin_batch()
        hits = client.search_hits(batch_queries)
        app_ids: list[str] = []
        seen_ids_now: set[str] = set()
        for ids in hits.values():
            for app_id in ids:
                if app_id in exclude or app_id in seen_ids_now:
                    continue
                seen_ids_now.add(app_id)
                app_ids.append(app_id)
        if not app_ids:
            print(f"đợt {index}: hết ứng viên mới")
            if index == 2 and not client.halted:
                retired.extend(_retire_queries(hits, productive, set(), exclude))
            continue
        batch_passed, batch_scanned, batch_opened, handled = shortlist(
            client,
            app_ids,
            catalog,
            seeds,
            publishers,
            chart_ids,
            play_catalog,
            play_seeds,
            ignore_tags,
            limit=DETAIL_CAP,
        )
        if index <= 2:
            opened_now = set(batch_opened)
            for query, ids in hits.items():
                if any(app_id in opened_now for app_id in ids):
                    productive.add(normalize_query(query))
        if index == 2 and not client.halted:
            retired.extend(_retire_queries(hits, productive, set(handled), exclude))
        passed.extend(batch_passed)
        scanned += batch_scanned
        opened.extend(batch_opened)
        exclude.update(handled)
        print(f"đợt {index}: ứng viên {len(app_ids)}, đã lấy chi tiết {batch_scanned}, qua lọc {len(batch_passed)}")
    if retired:
        print(f"bỏ câu tìm: {retired}")
    return passed, scanned, opened, retired


def _pull_ratings(data: SteamData, telegram: Telegram) -> tuple[dict, bool]:
    ratings = data.load_ratings()
    offset = data.load_offset()
    updates = telegram.get_updates(offset)
    found, next_offset = collect_ratings(updates, telegram.chat_id, message_index(data.load_catalog()))
    items = list(ratings.get("items") or [])
    existing = {(item.get("message_id"), item.get("score"), item.get("note")) for item in items}
    added = 0
    for item in found:
        key = (item.get("message_id"), item.get("score"), item.get("note"))
        if key not in existing:
            items.append(item)
            existing.add(key)
            added += 1
    ratings = {"items": items}
    changed = added > 0
    if changed or (updates and next_offset != offset):
        data.save_ratings(ratings)
        data.save_offset(max(offset, next_offset))
    print(f"Telegram Steam: {len(updates)} update, {added} điểm mới")
    return ratings, changed


def _exhausted_queries(data: SteamData) -> list[str]:
    return [str(item) for item in (data.load_queries().get("exhausted") or []) if str(item).strip()]


def _remember(catalog: dict, app: dict, message_id: int) -> None:
    app_id = str(app.get("appId"))
    title = str(app.get("title") or "")
    catalog.setdefault("apps", {})[app_id] = {
        "app_id": app_id,
        "title": title,
        "title_norm": normalize_title(title),
        "developer": app.get("developer"),
        "reviews": app.get("reviews"),
        "url": steam_url(app_id),
        "message_id": message_id,
        "suggested_at": datetime.now(timezone.utc).isoformat(),
    }


def _llm_paused(llm: LlmClient) -> bool:
    can_gemini = bool(llm.gemini_key) and not llm.guard.active("gemini")
    can_groq = bool(llm.groq_key) and not llm.guard.active("groq")
    return not can_gemini and not can_groq


def _notify_pending(guard: Guard, telegram: Telegram) -> None:
    pending = [
        name
        for name in ("steam", "gemini", "groq")
        if (guard.state.get(name) or {}).get("until") and not guard.state[name].get("notified")
    ]
    if not pending:
        return
    until = guard.state[pending[0]].get("until", "")
    try:
        telegram.send(f"Steam tạm dừng tới {until} vì bị giới hạn hoặc chặn: {', '.join(pending)}.")
    except Exception as exc:
        print(f"Không báo được Telegram: {exc}")
    for name in pending:
        guard.state[name]["notified"] = True


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if __name__ == "__main__":
    raise SystemExit(main())
