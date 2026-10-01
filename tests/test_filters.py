from datetime import date
from pathlib import Path

from mkresearch.filters import (
    hard_reject,
    in_install_band,
    is_puzzle,
    publisher_blocked,
    released_too_recent,
    strip_ignored,
    title_ignored,
)
from mkresearch.store import Store

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["Voodoo", "Lion Studios", "ABI Game Studio", "ABI", "Falcon"]


def _app(**overrides):
    base = {
        "appId": "com.example.puzzle",
        "title": "Odd Boxes",
        "developer": "DAN-BALL",
        "free": True,
        "minInstalls": 10_000,
        "genre": "Puzzle",
        "genreId": "GAME_PUZZLE",
    }
    base.update(overrides)
    return base


def test_install_band_keeps_mid_buckets():
    assert in_install_band(10_000)
    assert in_install_band(50_000)
    assert in_install_band(100_000)


def test_install_band_drops_tiny_and_hits():
    assert not in_install_band(1_000)
    assert not in_install_band(500_000)
    assert not in_install_band(1_000_000)
    assert not in_install_band(None)


def test_named_publishers_are_blocked_and_danball_is_not():
    assert publisher_blocked("Voodoo", NAMES)
    assert publisher_blocked("Lion Studios", NAMES)
    assert publisher_blocked("ABI Game Studio", NAMES)
    assert publisher_blocked("FALCON", NAMES)
    assert not publisher_blocked("DAN-BALL", NAMES)
    assert not publisher_blocked("Habib Games", NAMES)


def test_hard_reject_reasons():
    assert hard_reject(_app(minInstalls=100_000), NAMES, set()) is None
    assert hard_reject(_app(free=False), NAMES, set()) == "paid"
    assert hard_reject(_app(minInstalls=500_000), NAMES, set()) == "installs"
    assert hard_reject(_app(developer="Voodoo"), NAMES, set()) == "publisher"
    assert hard_reject(_app(appId="com.hit.game"), NAMES, {"com.hit.game"}) == "chart"
    assert hard_reject(_app(genre="Arcade", genreId="GAME_ARCADE"), NAMES, set()) == "genre"


def test_vietnamese_puzzle_genre_is_kept():
    assert is_puzzle({"genre": "Giải đố"})
    assert not is_puzzle({"genre": "Công cụ"})


def test_recent_release_years_are_dropped_and_unknown_dates_stay():
    today = date(2026, 9, 29)
    assert released_too_recent("Oct 12, 2024", today)
    assert released_too_recent("Jan 1, 2025", today)
    assert released_too_recent("Sep 1, 2026", today)
    assert not released_too_recent("Mar 1, 2019", today)
    assert not released_too_recent("Jan 1, 2023", today)
    assert not released_too_recent(None, today)
    assert not released_too_recent("soon", today)
    current = date.today().year
    assert hard_reject(_app(released=f"Jan 1, {current}"), NAMES, set()) == "recent"
    assert hard_reject(_app(released=f"Jan 1, {current - 3}"), NAMES, set()) is None
    assert hard_reject(_app(released=""), NAMES, set()) is None


def test_ignore_keywords_match_block_and_phrases_but_not_reptile():
    keywords = ["block", "arrow", "match 3", "tile", "screw"]
    assert title_ignored("Unblock Puzzle", keywords)
    assert title_ignored("Arrow Escape", keywords)
    assert title_ignored("Match-3 Garden", keywords)
    assert title_ignored("Tile Sort", keywords)
    assert not title_ignored("Reptile", keywords)
    assert not title_ignored("Snakebird", keywords)
    assert title_ignored("Furry SexyTails", ["sexy"])
    assert title_ignored("NSFW Gallery", ["nsfw"])
    assert hard_reject(_app(summary="A jigsaw of cute photos."), NAMES, set(), ["jigsaw"]) == "keyword"
    assert publisher_blocked("Do Games Limited", ["Do Games Limited"])
    assert hard_reject(_app(title="Block Puzzle"), NAMES, set(), keywords) == "keyword"
    names = Store(ROOT / "data").load_ignore_keywords()
    assert names[:5] == ["block", "arrow", "match 3", "tile", "screw"]
    assert {"sexy", "nsfw", "jigsaw"} <= set(names)
    assert strip_ignored("physics block placement puzzle levels", keywords) == "physics placement puzzle levels"
    assert strip_ignored("Match-3 Garden", keywords) == "Garden"
    assert strip_ignored("Reptile", keywords) == "Reptile"


def test_default_publisher_file_lists_the_named_studios():
    names = [name.casefold() for name in Store(ROOT / "data").load_publishers()]
    for needle in ("voodoo", "lion studios", "abi", "falcon", "do games limited"):
        assert any(needle == name or needle in name for name in names)
