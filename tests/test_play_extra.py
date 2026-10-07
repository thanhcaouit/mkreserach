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

    def send(self, text: str) -> int:
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


def test_extra_search_skips_play_data_and_sends_its_own_report(tmp_path: Path, capsys):
    _write_store(tmp_path)
    store = Store(tmp_path)
    extra = ExtraStore(tmp_path)
    play = FakePlay()
    play.details["new.id"] = _pass("new.id")
    play.details["cluster.id"] = _pass("cluster.id")
    telegram = FakeTelegram()

    order: list[str] = []

    def search(term: str) -> list[dict]:
        order.append("search")
        assert term == "orbit maze"
        return [{"appId": "old.id", "title": "Old"}, {"appId": "new.id", "title": "Maze new.id"}]

    def clusters(terms: list[str]) -> list[dict]:
        order.append("cluster")
        assert terms == ["orbit maze"]
        return [{"appId": "cluster.id", "title": "Maze cluster.id"}]

    code = run_extra(store, extra, play, telegram, search_fn=search, cluster_fn=clusters)
    assert code == 0
    assert order == ["cluster", "search"]
    assert json.loads((tmp_path / "play_seen.json").read_text())["apps"] == {"old.id": {"seen": "2026-10-01"}}
    assert "new.id" not in json.loads((tmp_path / "catalog.json").read_text())["apps"]
    saved = extra.load_catalog()["apps"]
    assert set(saved) == {"new.id", "cluster.id"}
    assert telegram.sent[0].startswith("kryuchenko\nPlay riêng\n")
    assert telegram.sent[1].startswith("MrAdex77\nPlay riêng\n")
    assert "Không có game mới" not in "\n".join(telegram.sent)
    assert "old.id" not in "\n".join(telegram.sent)
    assert "old.id" not in extra.load_seen()["apps"]
    assert set(extra.load_seen()["apps"]) == {"new.id", "cluster.id"}
    output = capsys.readouterr().out
    assert "kryuchenko qua lọc: Maze cluster.id | cluster.id | Solo | 20,000+" in output
    assert "MrAdex77 qua lọc: Maze new.id | new.id | Solo | 20,000+" in output


def test_extra_search_retires_a_query_that_only_hits_play_data():
    play = FakePlay()
    _sources, retired, _pending = search_extra(
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
    assert retired == ["orbit maze"]
    assert play.app_calls == 0


def test_extra_report_is_separate_from_the_play_report():
    text = format_extra_report(
        {"appId": "new.id", "title": "Maze", "developer": "Solo", "installs": "20,000+"},
        "MrAdex77",
    )
    assert text.startswith("MrAdex77\nPlay riêng\nMaze\n")


def test_each_package_reports_when_nothing_passes(tmp_path: Path, capsys):
    _write_store(tmp_path)
    store = Store(tmp_path)
    extra = ExtraStore(tmp_path)
    telegram = FakeTelegram()
    code = run_extra(
        store,
        extra,
        FakePlay(),
        telegram,
        search_fn=lambda _term: [{"appId": "old.id", "title": "Old"}],
        cluster_fn=lambda _terms: [],
    )
    assert code == 0
    assert telegram.sent == [
        "kryuchenko: Không có game mới. Đã xem 0 app, 0 game qua bộ lọc.",
        "MrAdex77: Không có game mới. Đã xem 0 app, 0 game qua bộ lọc.",
    ]
    output = capsys.readouterr().out
    assert "kryuchenko: ứng viên 0, đã lấy chi tiết 0, qua lọc 0" in output
    assert "MrAdex77: ứng viên 0, đã lấy chi tiết 0, qua lọc 0" in output


def test_known_bad_cards_are_not_opened_and_a_2024_puzzle_passes():
    play = FakePlay()
    play.details["ok.id"] = _pass("ok.id")
    sources, _retired, _pending = search_extra(
        play,
        ["orbit maze"],
        [],
        ["fun"],
        [],
        [],
        None,
        set(),
        {"apps": {}},
        [],
        [],
        set(),
        lambda _term: [
            {"appId": "paid.id", "title": "Paid", "free": False},
            {"appId": "big.id", "title": "Big", "free": True, "minInstalls": 1_000_000, "genreId": "GAME_PUZZLE"},
            {"appId": "word.id", "title": "Words", "free": True, "minInstalls": 20_000, "genreId": "GAME_WORD"},
            {"appId": "fun.id", "title": "Fun maze", "free": True, "minInstalls": 20_000, "genreId": "GAME_PUZZLE"},
            {"appId": "ok.id", "title": "Maze ok", "free": True, "minInstalls": 20_000, "genreId": "GAME_PUZZLE"},
        ],
        lambda _terms: [],
    )
    assert play.app_calls == 1
    assert [app["appId"] for app in sources[1]["passed"]] == ["ok.id"]
    assert sources[1]["scanned"] == 1


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
