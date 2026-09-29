from __future__ import annotations

import time

import httpx

from mkresearch.cooldown import Guard, limit_kind
from mkresearch.steam.discover import (
    APP_URL,
    DETAIL_CAP,
    PUZZLE_TAG,
    SEARCH_URL,
    DetailLimit,
    parse_app_details,
    parse_search_items,
)


class SteamClient:
    def __init__(self, guard: Guard | None = None, http: httpx.Client | None = None) -> None:
        self.guard = guard or Guard()
        self.http = http or httpx.Client(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"},
        )
        self.halted = bool(self.guard.active("steam"))
        self.detail_calls = 0

    def chart_ids(self) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for listing in ("globaltopsellers", "popularnew"):
            for item in self._search(filter=listing):
                app_id = item["appId"]
                if app_id not in seen:
                    seen.add(app_id)
                    found.append(app_id)
        return found

    def search_ids(self, queries: list[str]) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for query in queries[:6]:
            if self.halted:
                break
            for item in self._search(term=query):
                app_id = item["appId"]
                if app_id not in seen:
                    seen.add(app_id)
                    found.append(app_id)
        return found

    def begin_batch(self) -> None:
        self.detail_calls = 0

    def app_details(self, app_id: str) -> dict:
        if self.halted or self.guard.active("steam"):
            self.halted = True
            raise DetailLimit("Steam đang nghỉ")
        if self.detail_calls >= DETAIL_CAP:
            raise DetailLimit("Đã đủ chi tiết Steam trong lượt này")
        self.detail_calls += 1
        payload = self._get(APP_URL, {"appids": app_id, "l": "english", "cc": "us"})
        if payload is None:
            raise DetailLimit("Steam đang nghỉ")
        parsed = parse_app_details(app_id, payload)
        if parsed is None:
            return {"appId": str(app_id), "title": "", "type": "missing", "from_puzzle_tag": True}
        time.sleep(0.4)
        return parsed

    def _search(self, term: str = "", filter: str = "") -> list[dict]:
        params = {
            "json": 1,
            "ignore_preferences": 1,
            "category1": 998,
            "tags": PUZZLE_TAG,
            "cc": "us",
            "l": "english",
            "ndl": 1,
        }
        if term:
            params["term"] = term
        if filter:
            params["filter"] = filter
        payload = self._get(SEARCH_URL, params)
        if payload is None:
            return []
        return parse_search_items(payload)

    def _get(self, url: str, params: dict) -> dict | None:
        if self.halted or self.guard.active("steam"):
            self.halted = True
            return None
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            if limit_kind(exc) is not None:
                self.guard.trip("steam", "day", "HTTP giới hạn steam")
                self.halted = True
                return None
            raise
        return body if isinstance(body, dict) else None
