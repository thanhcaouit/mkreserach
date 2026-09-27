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
        "Game mẫu là puzzle theo màn, một luật rõ: Cross Virus (đặt block chặn virus lan chữ thập) "
        "và Lemmings (dẫn lemming qua từng màn).\n"
        "Loại match-3, screw puzzle, sort puzzle, idle, endless một vòng, hypercasual không màn.\n"
        "Chỉ trả JSON dạng "
        '{"picks":[{"app_id":"","has_levels":true,"novelty":1,"mechanic_vi":"","why_vi":"","near_seed":""}]}.\n'
        "novelty là số nguyên 1-5. has_levels true chỉ khi chơi theo màn hoặc mục tiêu từng màn.\n"
        "mechanic_vi và why_vi viết tiếng Việt, mỗi cái một hoặc hai câu.\n"
        "Chỉ đưa game thật sự có level và novelty từ 3 trở lên. Tối đa 3 game.\n\n"
        f"Hồ sơ gu:\n{_dump(profile)}\n\n"
        f"Seed:\n{_dump(seeds)}\n\n"
        f"Ứng viên:\n{_dump(packed)}\n"
    )


def select_picks(payload: dict, allowed_ids: set[str], limit: int = 3) -> list[dict]:
    picks = payload.get("picks")
    if not isinstance(picks, list):
        return []
    chosen: list[dict] = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        app_id = str(pick.get("app_id") or "")
        if app_id not in allowed_ids:
            continue
        if pick.get("has_levels") is not True:
            continue
        try:
            novelty = int(pick.get("novelty"))
        except (TypeError, ValueError):
            continue
        if novelty < 3:
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


def judge(llm: LlmClient, candidates: list[dict], seeds: list[dict], profile: dict, limit: int = 3) -> list[dict]:
    if not candidates:
        return []
    raw = llm.complete(build_judge_prompt(candidates, seeds, profile))
    allowed = {str(app.get("appId")) for app in candidates}
    return select_picks(parse_json_object(raw), allowed, limit)


def _dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
