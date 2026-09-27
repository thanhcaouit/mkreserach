from datetime import datetime, timedelta, timezone

from mkresearch.cooldown import Guard, ICT, cooldown_until, limit_kind


def test_day_limit_rests_until_next_nine():
    moment = datetime(2026, 9, 27, 10, 5, tzinfo=ICT)
    until = cooldown_until("day", moment)
    assert until == datetime(2026, 9, 28, 9, 0, tzinfo=ICT)


def test_before_nine_waits_until_this_morning():
    moment = datetime(2026, 9, 27, 8, 0, tzinfo=ICT)
    assert cooldown_until("day", moment) == datetime(2026, 9, 27, 9, 0, tzinfo=ICT)


def test_guard_skips_calls_until_expiry():
    now = datetime(2026, 9, 27, 11, 0, tzinfo=timezone.utc)
    guard = Guard()
    guard.trip("gemini", "day", "HTTP 429", now)
    assert guard.active("gemini", now + timedelta(hours=1))
    assert not guard.active("groq", now)
    later = datetime(2026, 9, 28, 2, 30, tzinfo=timezone.utc)
    assert not guard.active("gemini", later)


def test_status_codes_count_as_a_block():
    class Response:
        status_code = 429

    class Error(Exception):
        response = Response()

    assert limit_kind(Error()) == "day"
    assert limit_kind(Exception("App not found. Status code 403 returned.")) == "day"
    assert limit_kind(Exception("temporary 500")) is None
