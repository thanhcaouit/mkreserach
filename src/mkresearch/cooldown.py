from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

ICT = timezone(timedelta(hours=7))
LIMIT_STATUSES = {402, 403, 429}


class LimitReached(RuntimeError):
    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(f"{scope}: {reason}")
        self.scope = scope
        self.reason = reason


def limit_kind(exc: Exception) -> str | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        match = re.search(r"\b(402|403|429)\b", str(exc))
        status = int(match.group(1)) if match else None
    if status in LIMIT_STATUSES:
        return "day"
    if "PlayGatewayError" in str(exc):
        return "day"
    return None


def cooldown_until(kind: str, now: datetime) -> datetime:
    current = now.astimezone(ICT)
    if kind == "minute":
        return current + timedelta(minutes=70)
    morning = current.replace(hour=9, minute=0, second=0, microsecond=0)
    if current >= morning:
        morning += timedelta(days=1)
    return morning


class Guard:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state if state is not None else {}

    def active(self, scope: str, now: datetime | None = None) -> bool:
        entry = self.state.get(scope) or {}
        until = entry.get("until")
        if not until:
            return False
        moment = now or datetime.now(timezone.utc)
        return datetime.fromisoformat(until) > moment.astimezone(ICT)

    def trip(self, scope: str, kind: str, reason: str, now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        self.state[scope] = {
            "until": cooldown_until(kind, moment).isoformat(),
            "reason": reason,
            "notified": False,
        }

    def reason(self, scope: str) -> str:
        return str((self.state.get(scope) or {}).get("reason") or "bị giới hạn")
