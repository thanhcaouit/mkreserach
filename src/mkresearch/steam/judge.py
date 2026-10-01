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
                "reviews": app.get("reviews"),
                "price": app.get("price_text") or ("Free" if app.get("is_free") else ""),
                "genres": app.get("genres"),
                "description": str(app.get("description") or "")[:700],
            }
        )
    return (
        "Bạn chọn puzzle Steam có level và luật chơi lạ, gần tinh thần game mẫu.\n"
        "Game mẫu là Snakebird, Stephen's Sausage Roll, World of Goo và World of Goo 2: nhiều màn, một luật rõ.\n"
        "Mọi mức giá đều được. Loại match-3, screw puzzle, sort puzzle, idle, endless, "
        "game chơi một mạch không chia màn, jigsaw ghép ảnh, unblock thuần, mahjong cổ điển không có luật mới.\n"
        "Chỉ trả JSON dạng "
        '{"picks":[{"app_id":"","has_levels":true,"novelty":1,"near_seed":""}]}.\n'
        "novelty là số nguyên 1-5. has_levels true chỉ khi chơi theo màn hoặc mục tiêu từng màn.\n"
        "app_id phải chép đúng từ danh sách ứng viên. Không bịa id mới.\n"
        "near_seed là Snakebird, Stephen's Sausage Roll, World of Goo hoặc World of Goo 2.\n"
        "Trả mọi ứng viên có level trong danh sách, kể cả khi nhiều hơn 5. Xếp theo novelty, cao hơn đứng trước.\n\n"
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
                "near_seed": str(pick.get("near_seed") or "").strip(),
            }
        )
    chosen.sort(key=lambda item: item["novelty"], reverse=True)
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
        return []
    picks = select_picks(payload, allowed, limit)
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
