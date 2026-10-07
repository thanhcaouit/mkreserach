import json
from pathlib import Path

from mkresearch.cooldown import Guard
from mkresearch.play_extra import ExtraStore, format_extra_report, run_extra, search_extra
from mkresearch.store import Store


class FakePlay:
    def __init__(self) -> None:
        self.app_calls = 0
        self.app_limit = 40
        self.halted = False
        self.guard = Guard()
        self.details: dict[str, dict] = {}

    def suggestions(self, term: str) -> list[str]:
        return []

    def app_details(self, app_id: str, lang: str = "en", country: str = "us") -> dict:
        self.app_calls += 1
        return dict(self.details[app_id])


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_report(self, text: str, screenshot_url: str | None) -> int:
        self.sent.append(text)
        return len(self.sent)


def _pass(app_id: str) -> dict:
    return {
        "appId": app_id,
        "title": f"Maze {app_id}",
        "free": True,
        "minInstalls": 20_000,
        "genreId": "GAME_PUZZLE",
        "developer": "Solo",
        "installs": "20,000+",
        "released": "Jan 1, 2024",
    }


def test_extra_search_skips_play_data_and_sends_its_own_report(tmp_path: Path):
    _write_store(tmp_path)
    store = Store(tmp_path)
    extra = ExtraStore(tmp_path)
    play = FakePlay()
    play.details["new.id"] = _pass("new.id")
    play.details["cluster.id"] = _pass("cluster.id")
    telegram = FakeTelegram()

    def search(term: str) -> list[dict]:
        assert term == "orbit maze"
        return [{"appId": "old.id", "title": "Old"}, {"appId": "new.id", "title": "Maze new.id"}]

    def clusters(terms: list[str]) -> list[dict]:
        assert terms == ["orbit maze"]
        return [{"appId": "cluster.id", "title": "Maze cluster.id"}]

    code = run_extra(store, extra, play, telegram, search_fn=search, cluster_fn=clusters)
    assert code == 0
    assert json.loads((tmp_path / "play_seen.json").read_text())["apps"] == {"old.id": {"seen": "2026-10-01"}}
    assert "new.id" not in json.loads((tmp_path / "catalog.json").read_text())["apps"]
    saved = extra.load_catalog()["apps"]
    assert set(saved) == {"new.id", "cluster.id"}
    assert all(text.startswith("Play riêng\n") for text in telegram.sent)
    assert "old.id" not in "\n".join(telegram.sent)
    assert "old.id" not in extra.load_seen()["apps"]
    assert set(extra.load_seen()["apps"]) == {"new.id", "cluster.id"}


def test_extra_search_retires_a_query_that_only_hits_play_data():
    play = FakePlay()
    passed, scanned, opened, retired, _pending = search_extra(
        play,
        ["orbit maze"],
        [],
        [],
        [],
        [],
        None,
        {"old.id"},
        {"apps": {}},
        [],
        [],
        set(),
        lambda _term: [{"appId": "old.id"}],
        lambda _terms: [],
    )
    assert passed == []
    assert scanned == 0
    assert opened == []
    assert retired == ["orbit maze"]
    assert play.app_calls == 0


def test_extra_report_is_separate_from_the_play_report():
    text = format_extra_report({"appId": "new.id", "title": "Maze", "developer": "Solo", "installs": "20,000+"})
    assert text.startswith("Play riêng\nMaze\n")


def _write_store(root: Path) -> None:
    (root / "seeds.yaml").write_text("games: []\n", encoding="utf-8")
    (root / "publishers.yaml").write_text("names: []\n", encoding="utf-8")
    (root / "ignore_keywords.yaml").write_text("keywords: []\n", encoding="utf-8")
    (root / "search_words.yaml").write_text("words: []\n", encoding="utf-8")
    (root / "profile.json").write_text(
        json.dumps({"search_queries": ["orbit maze"], "liked_mechanics": []}),
        encoding="utf-8",
    )
    (root / "play_seen.json").write_text(
        json.dumps({"apps": {"old.id": {"seen": "2026-10-01"}}}),
        encoding="utf-8",
    )
    (root / "catalog.json").write_text(json.dumps({"apps": {}}), encoding="utf-8")
    (root / "chart_blocklist.json").write_text(json.dumps({"apps": {}}), encoding="utf-8")
