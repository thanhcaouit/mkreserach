from __future__ import annotations

from datetime import datetime, timezone

from mkresearch.llm import LlmClient, parse_json_object


def latest_by_app(items: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for item in items:
        app_id = str(item.get("package_id") or "")
        if app_id:
            latest[app_id] = item
    return latest


def anchor_ids(seeds: list[dict], ratings: list[dict], extra: list[str] | None = None) -> list[str]:
    ordered: list[str] = []
    for seed in seeds:
        app_id = str(seed.get("app_id") or "")
        if app_id and app_id not in ordered:
            ordered.append(app_id)
    for app_id, item in latest_by_app(ratings).items():
        if int(item.get("score") or 0) >= 4 and app_id not in ordered:
            ordered.append(app_id)
    for app_id in extra or []:
        if app_id and app_id not in ordered:
            ordered.append(app_id)
    return ordered


def build_learn_prompt(seeds: list[dict], ratings: list[dict], profile: dict) -> str:
    latest = list(latest_by_app(ratings).values())
    return (
        "Bạn cập nhật hồ sơ gu tìm puzzle trên Steam.\n"
        "Chỉ trả JSON với các khóa liked_mechanics, disliked_mechanics, search_queries, notes.\n"
        "search_queries là 4 đến 6 cụm tiếng Anh để tìm trên Steam, hướng tới puzzle có level, "
        "một luật chơi rõ. Tránh match-3, screw, sort, idle, endless.\n"
        "Điểm 4-5 là thích. Điểm 1-2 là không thích.\n\n"
        f"Seed:\n{_dump(seeds)}\n\n"
        f"Hồ sơ hiện tại:\n{_dump(profile)}\n\n"
        f"Điểm mới nhất theo game:\n{_dump(latest)}\n"
    )


def apply_profile(current: dict, payload: dict, seeds: list[dict], ratings: list[dict]) -> dict:
    liked = _string_list(payload.get("liked_mechanics")) or list(current.get("liked_mechanics") or [])
    disliked = _string_list(payload.get("disliked_mechanics")) or list(current.get("disliked_mechanics") or [])
    queries = _string_list(payload.get("search_queries")) or list(current.get("search_queries") or [])
    return {
        "liked_mechanics": liked[:8],
        "disliked_mechanics": disliked[:8],
        "search_queries": queries[:6],
        "anchor_app_ids": anchor_ids(seeds, ratings, list(current.get("anchor_app_ids") or [])),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "notes": str(payload.get("notes") or ""),
    }


def update_profile(llm: LlmClient, seeds: list[dict], ratings: list[dict], profile: dict) -> dict:
    if not ratings:
        profile = dict(profile)
        profile["anchor_app_ids"] = anchor_ids(seeds, ratings, list(profile.get("anchor_app_ids") or []))
        return profile
    raw = llm.complete(build_learn_prompt(seeds, ratings, profile))
    return apply_profile(profile, parse_json_object(raw), seeds, ratings)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
