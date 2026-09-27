from __future__ import annotations


def steam_url(app_id: str) -> str:
    return f"https://store.steampowered.com/app/{app_id}"


def first_screenshot(app: dict) -> str | None:
    shots = app.get("screenshots") or []
    if not isinstance(shots, list):
        return None
    for shot in shots:
        if isinstance(shot, str) and shot.startswith("http"):
            return shot
    return None


def format_report(app: dict, judgement: dict) -> str:
    title = app.get("title") or app.get("appId")
    developer = app.get("developer") or "Không rõ"
    reviews = app.get("reviews")
    review_text = f"{reviews} review" if isinstance(reviews, int) else "chưa rõ review"
    price = "Free" if app.get("is_free") else (app.get("price_text") or "có giá")
    app_id = str(app.get("appId") or "")
    near = judgement.get("near_seed") or "game mẫu"
    return (
        "Steam\n"
        f"{title}\n"
        f"{developer} · {review_text} · {price}\n"
        f"{steam_url(app_id)}\n\n"
        f"Gần mẫu: {near}\n\n"
        "Chấm điểm: reply tin này bằng 1–5. Có thể thêm một câu."
    )
