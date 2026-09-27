from __future__ import annotations

from mkresearch.llm import LlmClient, parse_json_object


def build_judge_prompt(candidates: list[dict], seeds: list[dict], profile: dict) -> str:
    packed = []
    for app in candidates:
        packed.append(
            {
                "app_id": app.get("appId"),
                "title": app.get("title"),
                "developer": app.get("developer"),
                "installs": app.get("installs") or app.get("minInstalls"),
                "genre": app.get("genre"),
                "description": str(app.get("description") or "")[:700],
            }
        )
    return (
        "Bạn chọn puzzle mobile có level và luật chơi lạ, gần tinh thần game mẫu.\n"
        "Game mẫu là puzzle theo màn, một luật rõ: Cross Virus (đặt block chặn virus lan chữ thập), "
        "Lemmings (dẫn lemming qua từng màn) và Slimbo (thả khối nhão dựng đường dẫn goo tới kẹo).\n"
        "Loại match-3, screw puzzle, sort puzzle, idle, endless một vòng, jigsaw ghép ảnh, unblock thuần, mahjong/shisen cổ điển không có luật mới.\n"
        "Chỉ trả JSON dạng "
        '{"picks":[{"app_id":"","has_levels":true,"novelty":1,"mechanic_vi":"","why_vi":"","near_seed":""}]}.\n'
        "novelty là số nguyên 1-5. has_levels true chỉ khi chơi theo màn hoặc mục tiêu từng màn.\n"
        "app_id phải chép đúng từ danh sách ứng viên. Không bịa id mới.\n"
        "mechanic_vi và why_vi viết tiếng Việt, mỗi cái một hoặc hai câu.\n"
        "Trả đúng 5 game có level nếu danh sách ứng viên đủ 5. Xếp theo novelty, cao hơn đứng trước.\n\n"
        f"Hồ sơ gu:\n{_dump(profile)}\n\n"
        f"Seed:\n{_dump(seeds)}\n\n"
        f"Ứng viên:\n{_dump(packed)}\n"
    )


def select_picks(payload: dict, allowed_ids: set[str], limit: int = 5) -> list[dict]:
    picks = payload.get("picks")
    if not isinstance(picks, list):
        return []
    chosen: list[dict] = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        app_id = str(pick.get("app_id") or pick.get("appId") or "").strip()
        if app_id not in allowed_ids:
            continue
        if not _as_bool(pick.get("has_levels")):
            continue
        try:
            novelty = int(float(pick.get("novelty")))
        except (TypeError, ValueError):
            continue
        if novelty < 1:
            continue
        chosen.append(
            {
                "app_id": app_id,
                "has_levels": True,
                "novelty": novelty,
                "mechanic_vi": str(pick.get("mechanic_vi") or "").strip(),
                "why_vi": str(pick.get("why_vi") or "").strip(),
                "near_seed": str(pick.get("near_seed") or "").strip(),
            }
        )
    chosen.sort(key=lambda item: item["novelty"], reverse=True)
    return chosen[:limit]


def complete_picks(picks: list[dict], candidates: list[dict], limit: int = 5) -> list[dict]:
    chosen = list(picks)
    have = {item["app_id"] for item in chosen}
    for app in candidates:
        if len(chosen) >= limit:
            break
        app_id = str(app.get("appId") or "")
        if not app_id or app_id in have:
            continue
        description = " ".join(str(app.get("description") or "").split())
        chosen.append(
            {
                "app_id": app_id,
                "has_levels": True,
                "novelty": 3,
                "mechanic_vi": description[:220] or "Level-based puzzle.",
                "why_vi": "Đã qua bộ lọc: puzzle miễn phí, khoảng 10k–500k lượt tải.",
                "near_seed": "",
            }
        )
        have.add(app_id)
    return chosen[:limit]


def judge(llm: LlmClient, candidates: list[dict], seeds: list[dict], profile: dict, limit: int = 5) -> list[dict]:
    if not candidates:
        return []
    raw = llm.complete(build_judge_prompt(candidates, seeds, profile))
    allowed = {str(app.get("appId")) for app in candidates}
    try:
        payload = parse_json_object(raw)
    except Exception as exc:
        print(f"LLM JSON lỗi: {exc}. Raw: {raw[:800]}")
        return complete_picks([], candidates, limit)
    picks = complete_picks(select_picks(payload, allowed, limit), candidates, limit)
    if not picks:
        print(f"LLM không chọn game. Raw: {raw[:800]}")
    return picks


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "có", "co"}
    return False


def _dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
