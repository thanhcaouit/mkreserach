from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from mkresearch.cooldown import Guard, LimitReached
from mkresearch.dedupe import normalize_title
from mkresearch.discover import (
    PlayStore,
    blocked_ids,
    gather_passed,
    merge_blocklist,
    recent_match_titles,
    remember_queries,
    remember_seen,
    seen_ids,
)
from mkresearch.filters import in_install_band
from mkresearch.judge import judge
from mkresearch.learn import update_profile
from mkresearch.llm import LlmClient
from mkresearch.store import Store
from mkresearch.telegram import (
    Telegram,
    collect_ratings,
    first_screenshot,
    format_report,
    message_index,
    play_url,
)


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(".env"))
    _load_dotenv(Path(".env.local"))
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"research", "ingest", "smoke"}:
        print("Dùng: python -m mkresearch.main research|ingest|smoke", file=sys.stderr)
        return 2
    mode = args[0]
    store = Store(Path(os.environ.get("DATA_DIR", "data")))
    guard = Guard(store.load_cooldown())
    telegram = Telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    llm = LlmClient(guard=guard)
    play = PlayStore(guard=guard)
    try:
        if mode == "ingest":
            code = run_ingest(store, telegram, llm)
        elif mode == "smoke":
            code = run_smoke(store, play, llm, telegram)
        else:
            code = run_research(store, play, llm, telegram)
        _notify_pending(guard, telegram)
        store.save_cooldown(guard.state)
        return code
    except LimitReached as exc:
        _notify_pending(guard, telegram)
        store.save_cooldown(guard.state)
        print(f"Nghỉ gọi dịch vụ: {exc}")
        return 0
    except Exception as exc:
        store.save_cooldown(guard.state)
        print(f"Lỗi: {exc}", file=sys.stderr)
        traceback.print_exc()
        if mode == "research" and telegram.configured():
            try:
                telegram.send(f"Research lỗi: {exc}")
            except Exception as send_exc:
                print(f"Không gửi được Telegram: {send_exc}", file=sys.stderr)
        return 1


def run_ingest(store: Store, telegram: Telegram, llm: LlmClient) -> int:
    _require_telegram(telegram)
    ratings, changed = _pull_ratings(store, telegram)
    if changed and not _llm_paused(llm):
        profile = update_profile(
            llm,
            store.load_seeds(),
            ratings["items"],
            store.load_profile(),
            _exhausted_queries(store),
        )
        store.save_profile(profile)
    elif changed:
        print("Có điểm mới, AI đang nghỉ nên chưa viết lại hồ sơ.")
    print(f"Đã đọc điểm. Tổng {len(ratings['items'])} dòng.")
    return 0


def run_research(store: Store, play: PlayStore, llm: LlmClient, telegram: Telegram) -> int:
    _require_telegram(telegram)
    if not llm.available():
        raise RuntimeError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
    seeds = store.load_seeds()
    if not seeds:
        raise RuntimeError("data/seeds.yaml chưa có game mẫu")
    if llm.guard.active("play") or _llm_paused(llm):
        print("Đang trong thời gian nghỉ, không gọi Play và AI.")
        _pull_ratings(store, telegram)
        return 0
    ratings, changed = _pull_ratings(store, telegram)
    profile = store.load_profile()
    if changed and not _llm_paused(llm):
        try:
            profile = update_profile(llm, seeds, ratings["items"], profile, _exhausted_queries(store))
            store.save_profile(profile)
        except LimitReached:
            raise
        except Exception as exc:
            print(f"Giữ hồ sơ cũ vì không cập nhật được: {exc}")
            profile = store.load_profile()

    charts = play.chart_ids()
    blocklist = merge_blocklist(store.load_blocklist(), charts, datetime.now(timezone.utc).date().isoformat())
    store.save_blocklist(blocklist)
    if play.halted:
        print("Play bị chặn, dừng lượt này.")
        return 0
    chart_ids = blocked_ids(blocklist)

    catalog = store.load_catalog()
    publishers = store.load_publishers()
    anchors = list(profile.get("anchor_app_ids") or [])
    keywords = store.load_ignore_keywords()
    queries = [str(item) for item in profile.get("search_queries") or []]
    seen = store.load_seen()
    saved_queries = store.load_queries()
    passed, scanned, opened, retired = gather_passed(
        play,
        anchors,
        queries,
        catalog,
        seeds,
        publishers,
        chart_ids,
        seen_ids(seen),
        recent_match_titles(catalog),
        keywords,
        _exhausted_queries(store),
        store.load_search_words(),
    )
    store.save_seen(remember_seen(seen, opened, datetime.now(timezone.utc).date().isoformat()))
    store.save_queries(remember_queries(saved_queries, retired))
    if play.halted and not passed:
        print("Play bị chặn, dừng lượt này.")
        return 0
    print(f"Đã lấy chi tiết {scanned}, qua lọc {len(passed)}")
    for app in passed:
        print(
            f"qua lọc: {app.get('title')} | {app.get('appId')} | "
            f"{app.get('developer')} | {app.get('installs')}"
        )
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
        _remember(catalog, app, message_id, pick)
        store.save_catalog(catalog)
        sent += 1
    if sent == 0:
        telegram.send(f"Không có game mới. Đã xem {scanned} app, {len(passed)} game qua bộ lọc.")
    print(f"Đã gửi {sent} game")
    return 0


def run_smoke(store: Store, play: PlayStore, llm: LlmClient, telegram: Telegram) -> int:
    _require_telegram(telegram)
    if not llm.available():
        raise RuntimeError("Thiếu GEMINI_API_KEY hoặc GROQ_API_KEY")
    catalog_path = store.root / "catalog.json"
    before = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
    seeds = {str(seed["app_id"]) for seed in store.load_seeds()}
    missing = {"jp.danball.crossvirus", "com.sadpuppy.lemmings"} - seeds
    if missing:
        print("Smoke thiếu seed: " + ", ".join(sorted(missing)))
        return 1
    cross = play.app_details("jp.danball.crossvirus")
    lemmings = play.app_details("com.sadpuppy.lemmings")
    failures: list[str] = []
    if not cross.get("free"):
        failures.append("Cross Virus không free")
    if "dan-ball" not in str(cross.get("developer") or "").casefold():
        failures.append(f"developer Cross Virus là {cross.get('developer')}")
    installs = cross.get("minInstalls")
    if not in_install_band(installs if isinstance(installs, int) else None):
        failures.append(f"minInstalls Cross Virus là {installs}")
    if not lemmings.get("title"):
        failures.append("không tải được Lemmings")
    if failures:
        print("Smoke seed lỗi: " + "; ".join(failures))
        return 1
    print(
        f"seed jp.danball.crossvirus ok free={cross.get('free')} "
        f"developer={cross.get('developer')} minInstalls={cross.get('minInstalls')}"
    )
    print(f"seed com.sadpuppy.lemmings ok title={lemmings.get('title')}")
    reply = llm.ping()
    print(f"llm {llm.last_model}: {reply}")
    if "ok" not in reply.casefold():
        print("Smoke LLM không trả về ok")
        return 1
    links = "\n".join(
        play_url(app_id) for app_id in ("jp.danball.crossvirus", "com.sadpuppy.lemmings")
    )
    message_id = telegram.send(f"smoke ok\n{links}")
    print(f"telegram message_id={message_id}")
    after = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
    if after != before:
        print("Smoke đã đổi catalog.json")
        return 1
    print("catalog unchanged")
    return 0


def _pull_ratings(store: Store, telegram: Telegram) -> tuple[dict, bool]:
    offset = store.load_offset()
    updates = telegram.get_updates(offset)
    catalog = store.load_catalog()
    found, next_offset = collect_ratings(updates, telegram.chat_id, message_index(catalog))
    ratings = store.load_ratings()
    items = list(ratings.get("items") or [])
    existing = {
        (item.get("message_id"), item.get("score"), item.get("note"))
        for item in items
    }
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
        store.save_ratings(ratings)
        store.save_offset(max(offset, next_offset))
    print(f"Telegram: {len(updates)} update, {added} điểm mới")
    return ratings, changed


def _exhausted_queries(store: Store) -> list[str]:
    return [str(item) for item in (store.load_queries().get("exhausted") or []) if str(item).strip()]


def _remember(catalog: dict, app: dict, message_id: int, pick: dict) -> None:
    app_id = str(app.get("appId"))
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
        "mechanic_vi": pick.get("mechanic_vi"),
        "why_vi": pick.get("why_vi"),
    }


def _llm_paused(llm: LlmClient) -> bool:
    can_gemini = bool(llm.gemini_key) and not llm.guard.active("gemini")
    can_groq = bool(llm.groq_key) and not llm.guard.active("groq")
    return not can_gemini and not can_groq


def _notify_pending(guard: Guard, telegram: Telegram) -> None:
    pending = [
        name
        for name in ("play", "gemini", "groq")
        if (guard.state.get(name) or {}).get("until") and not guard.state[name].get("notified")
    ]
    if not pending:
        return
    until = guard.state[pending[0]].get("until", "")
    try:
        telegram.send(f"Tạm dừng tới {until} vì bị giới hạn hoặc chặn: {', '.join(pending)}.")
    except Exception as exc:
        print(f"Không báo được Telegram: {exc}")
    for name in pending:
        guard.state[name]["notified"] = True


def _require_telegram(telegram: Telegram) -> None:
    if not telegram.configured():
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")


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
