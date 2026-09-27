from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx


SCORE_RE = re.compile(r"^\s*([1-5])(?!\d)(?:\s*[-–:]?\s*(\S.*?))?\s*$")


def parse_score_reply(text: str) -> tuple[int, str] | None:
    match = SCORE_RE.match(text or "")
    if not match:
        return None
    note = (match.group(2) or "").strip()
    return int(match.group(1)), note


def collect_ratings(
    updates: list[dict],
    chat_id: str,
    message_to_package: dict[int, str],
) -> tuple[list[dict], int]:
    ratings: list[dict] = []
    next_offset = 0
    chat = str(chat_id)
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        next_offset = max(next_offset, update_id + 1)
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id", "")) != chat:
            continue
        if (message.get("from") or {}).get("is_bot"):
            continue
        reply = message.get("reply_to_message") or {}
        reply_id = reply.get("message_id")
        if reply_id is None:
            continue
        package_id = message_to_package.get(int(reply_id))
        if not package_id:
            continue
        parsed = parse_score_reply(str(message.get("text") or ""))
        if parsed is None:
            continue
        score, note = parsed
        ratings.append(
            {
                "package_id": package_id,
                "score": score,
                "note": note,
                "message_id": int(reply_id),
                "rated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return ratings, next_offset


class Telegram:
    def __init__(self, token: str, chat_id: str, http: httpx.Client | None = None) -> None:
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.http = http or httpx.Client(timeout=30)

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> int:
        return self._post("sendMessage", {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})

    def send_report(self, text: str, screenshot_url: str | None) -> int:
        caption = text if len(text) <= 1024 else text[:1021] + "..."
        if screenshot_url:
            try:
                image = self.http.get(screenshot_url, timeout=30, follow_redirects=True)
                image.raise_for_status()
                response = self.http.post(
                    f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"photo": ("screenshot.jpg", image.content, image.headers.get("content-type", "image/jpeg"))},
                )
                return self._message_id(response)
            except Exception as exc:
                print(f"Không gửi được ảnh, gửi chữ: {exc}")
        return self.send(text)

    def _post(self, method: str, payload: dict) -> int:
        response = self.http.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        return self._message_id(response)

    def _message_id(self, response: httpx.Response) -> int:
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError("Telegram từ chối tin nhắn")
        return int(body["result"]["message_id"])

    def get_updates(self, offset: int) -> list[dict]:
        response = self.http.get(
            f"https://api.telegram.org/bot{self.token}/getUpdates",
            params={
                "offset": offset,
                "timeout": 0,
                "allowed_updates": json.dumps(["message"]),
            },
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(json.dumps(body))
        return list(body.get("result") or [])


def message_index(catalog: dict) -> dict[int, str]:
    index: dict[int, str] = {}
    for app_id, app in (catalog.get("apps") or {}).items():
        message_id = app.get("message_id")
        if message_id is None:
            continue
        index[int(message_id)] = str(app_id)
    return index


def play_url(app_id: str) -> str:
    return f"https://play.google.com/store/apps/details?id={app_id}&hl=en"


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
    installs = app.get("installs") or _install_label(app.get("minInstalls"))
    score = app.get("score")
    score_text = f"{float(score):.1f}★" if isinstance(score, (int, float)) else "chưa có điểm"
    app_id = app.get("appId") or app.get("app_id")
    url = play_url(str(app_id))
    iap = "Có IAP" if app.get("offersIAP") else "Không IAP"
    near = judgement.get("near_seed") or "game mẫu"
    return (
        f"{title}\n"
        f"{developer} · {installs} · {score_text}\n"
        f"{url}\n\n"
        f"Gần mẫu: {near}\n"
        f"{iap}\n\n"
        "Chấm điểm: reply tin này bằng 1–5. Có thể thêm một câu."
    )


def _install_label(min_installs: object) -> str:
    if isinstance(min_installs, int):
        return f"{min_installs:,}+"
    return "không rõ lượt tải"
