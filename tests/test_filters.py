from pathlib import Path

from mkresearch.filters import hard_reject, in_install_band, publisher_blocked
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


def test_default_publisher_file_lists_the_named_studios():
    names = [name.casefold() for name in Store(ROOT / "data").load_publishers()]
    for needle in ("voodoo", "lion studios", "abi", "falcon"):
        assert any(needle == name or needle in name for name in names)
