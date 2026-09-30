import random
from pathlib import Path

from mkresearch.discover import (
    DetailLimit,
    choose_random_query,
    gather_passed,
    recent_match_titles,
    remember_queries,
    remember_seen,
)
from mkresearch.filters import title_ignored
from mkresearch.learn import apply_profile, build_learn_prompt
from mkresearch.steam.store import SteamData
from mkresearch.store import Store


def _fail(app_id: str) -> dict:
    return {
        "appId": app_id,
        "title": app_id,
        "free": True,
        "minInstalls": 1000,
        "genreId": "GAME_PUZZLE",
        "developer": "Solo",
        "installs": "1,000+",
    }


def _pass(app_id: str, title: str | None = None) -> dict:
    detail = _fail(app_id)
    detail["title"] = title or app_id
    detail["minInstalls"] = 50_000
    detail["installs"] = "50,000+"
    return detail


class FakePlay:
    def __init__(self) -> None:
        self.app_calls = 0
        self.app_limit = 80
        self.halted = False
        self.similar: dict[str, list[str]] = {}
        self.pages: dict[str, list[str]] = {}
        self.details: dict[str, dict] = {}
        self.opened: list[str] = []
        self.searches: list[tuple[str, int]] = []

    def similar_ids(self, app_id: str, lang: str, country: str) -> list[str]:
        return list(self.similar.get(app_id, []))

    def search_apps(self, term: str, lang: str, country: str, n_hits: int = 15) -> list[dict]:
        self.searches.append((term, n_hits))
        return [{"appId": app_id, "title": app_id} for app_id in self.pages.get(term, [])[:n_hits]]

    def app_details(self, app_id: str, lang: str = "en", country: str = "us") -> dict:
        if self.app_calls >= self.app_limit:
            raise DetailLimit("cap")
        self.app_calls += 1
        self.opened.append(app_id)
        return dict(self.details[app_id])


def _catalog(titles: list[tuple[str, str]]) -> dict:
    return {
        "apps": {
            f"id.{index}": {"title": title, "suggested_at": stamp}
            for index, (title, stamp) in enumerate(titles)
        }
    }


def test_one_passer_keeps_searching_until_five():
    play = FakePlay()
    fails = [f"fail.{index}" for index in range(80)]
    for app_id in fails:
        play.details[app_id] = _fail(app_id)
    play.similar["anchor"] = fails
    play.pages["indie puzzle"] = ["pass.1"]
    play.details["pass.1"] = _pass("pass.1", "Fresh Rule")
    play.pages["Spooky Express"] = ["later.1"]
    play.details["later.1"] = _fail("later.1")
    catalog = _catalog([("Spooky Express", "2026-09-27T00:00:00Z")])
    passed, scanned, opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["indie puzzle"],
        catalog,
        [],
        [],
        set(),
        set(),
        recent_match_titles(catalog),
    )
    assert [app["appId"] for app in passed] == ["pass.1"]
    assert scanned == 82
    assert opened[-1] == "later.1"
    assert len(opened) == len(set(opened)) == 82
    assert ("indie puzzle", 40) in play.searches
    assert ("Spooky Express", 20) in play.searches


def test_batch_two_over_five_returns_all_and_skips_the_third():
    play = FakePlay()
    play.similar["anchor"] = ["early"]
    play.details["early"] = _pass("early", "Early")
    misses = [f"miss.{index}" for index in range(15)]
    hits = [f"hit.{index}" for index in range(6)]
    for app_id in misses:
        play.details[app_id] = _fail(app_id)
    for app_id in hits:
        play.details[app_id] = _pass(app_id)
    play.pages["indie puzzle"] = misses + hits
    play.pages["Spooky Express"] = ["later.1"]
    play.details["later.1"] = _pass("later.1", "Later")
    catalog = _catalog([("Spooky Express", "2026-09-27T00:00:00Z")])
    passed, _scanned, opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["indie puzzle"],
        catalog,
        [],
        [],
        set(),
        set(),
        recent_match_titles(catalog),
    )
    assert [app["appId"] for app in passed] == ["early", *hits]
    assert "later.1" not in opened
    assert all(n_hits != 20 for _term, n_hits in play.searches)


def test_short_batch_two_continues_and_batch_three_returns_all():
    play = FakePlay()
    misses = [f"miss.{index}" for index in range(15)]
    for app_id in misses:
        play.details[app_id] = _fail(app_id)
    for app_id in ("b.1", "b.2"):
        play.details[app_id] = _pass(app_id)
    third = [f"c.{index}" for index in range(6)]
    for app_id in third:
        play.details[app_id] = _pass(app_id)
    play.pages["q"] = [*misses, "b.1", "b.2"]
    play.pages["Newest"] = third
    catalog = _catalog([("Newest", "2026-09-27T00:00:00Z")])
    passed, _scanned, opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["q"],
        catalog,
        [],
        [],
        set(),
        set(),
        recent_match_titles(catalog),
    )
    assert [app["appId"] for app in passed] == ["b.1", "b.2", *third]
    assert opened[-len(third):] == third


def test_three_batches_when_nothing_passes_and_ids_stay_unique():
    play = FakePlay()
    play.similar["anchor"] = ["sim.1"]
    play.pages["q"] = [f"q.{index}" for index in range(20)]
    play.pages["Newest"] = ["title.1"]
    for app_id in ["sim.1", "title.1", *[f"q.{index}" for index in range(20)]]:
        play.details[app_id] = _fail(app_id)
    catalog = _catalog([("Newest", "2026-09-27T00:00:00Z")])
    passed, _scanned, opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["q"],
        catalog,
        [],
        [],
        set(),
        set(),
        recent_match_titles(catalog),
    )
    assert passed == []
    assert opened == ["sim.1", *[f"q.{index}" for index in range(20)], "title.1"]
    assert ("Newest", 20) in play.searches


def test_empty_second_batch_does_not_reopen_the_first():
    play = FakePlay()
    play.similar["anchor"] = ["sim.1"]
    play.pages["q"] = ["q.1"]
    play.pages["Newest"] = ["title.1"]
    for app_id in ("sim.1", "q.1", "title.1"):
        play.details[app_id] = _fail(app_id)
    catalog = _catalog([("Newest", "2026-09-27T00:00:00Z")])
    _passed, _scanned, opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["q"],
        catalog,
        [],
        [],
        set(),
        set(),
        recent_match_titles(catalog),
    )
    assert opened == ["sim.1", "q.1", "title.1"]


def test_third_batch_uses_the_six_newest_catalog_titles():
    play = FakePlay()
    titles = [(f"Game {index}", f"2026-09-{index + 1:02d}T00:00:00Z") for index in range(7)]
    catalog = _catalog(titles)
    gather_passed(play, ["anchor"], ["q"], catalog, [], [], set(), set(), recent_match_titles(catalog))
    title_searches = [term for term, n_hits in play.searches if n_hits == 20]
    assert title_searches == [f"Game {index}" for index in range(6, 0, -1)]
    assert "Game 0" not in title_searches


def test_previously_seen_ids_are_not_opened_again():
    play = FakePlay()
    play.similar["anchor"] = ["old.id", "new.id"]
    play.details["old.id"] = _fail("old.id")
    play.details["new.id"] = _fail("new.id")
    _passed, _scanned, opened, _retired = gather_passed(
        play, ["anchor"], [], {"apps": {}}, [], [], set(), {"old.id"}, []
    )
    assert opened == ["new.id"]


def test_remember_seen_keeps_the_first_day(tmp_path):
    updated = remember_seen({"apps": {"a": {"seen": "2026-09-01"}}}, ["a", "b"], "2026-09-28")
    assert updated["apps"]["a"]["seen"] == "2026-09-01"
    assert updated["apps"]["b"]["seen"] == "2026-09-28"
    store = Store(tmp_path)
    store.save_seen(updated)
    assert store.load_seen()["apps"]["b"]["seen"] == "2026-09-28"


def test_query_with_no_new_app_is_retired_and_skipped_next_run():
    play = FakePlay()
    play.pages["stale query"] = ["old.id"]
    _passed, _scanned, opened, retired = gather_passed(
        play, ["anchor"], ["stale query"], {"apps": {}}, [], [], set(), {"old.id"}, []
    )
    assert opened == []
    assert retired == ["stale query"]
    assert ("stale query", 40) in play.searches

    play.searches.clear()
    _passed, _scanned, opened, retired_again = gather_passed(
        play,
        ["anchor"],
        ["stale query"],
        {"apps": {}},
        [],
        [],
        set(),
        {"old.id"},
        [],
        exhausted=retired,
    )
    assert opened == []
    assert retired_again == []
    assert all(term != "stale query" for term, _n_hits in play.searches)


def test_each_run_searches_one_new_word():
    play = FakePlay()
    words = ["gravity", "laser", "rope"]
    expected = random.Random(0).choice([f"{word} puzzle levels" for word in words])
    _passed, _scanned, _opened, _retired = gather_passed(
        play,
        ["anchor"],
        ["profile query"],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        [],
        search_words=words,
        rng=random.Random(0),
    )
    extra = {term for term, _n_hits in play.searches if term.endswith(" puzzle levels")}
    assert extra == {expected}
    assert ("profile query", 15) in play.searches
    assert ("profile query", 40) in play.searches


def test_five_games_do_not_retire_a_query_before_the_wide_search():
    play = FakePlay()
    hits = [f"p.{index}" for index in range(5)]
    play.similar["anchor"] = hits
    for app_id in hits:
        play.details[app_id] = _pass(app_id)
    play.pages["quiet query"] = ["old.id"]
    play.details["old.id"] = _fail("old.id")
    _passed, _scanned, _opened, retired = gather_passed(
        play, ["anchor"], ["quiet query"], {"apps": {}}, [], [], set(), set(), ["Later Title"]
    )
    assert retired == []
    assert ("quiet query", 15) in play.searches
    assert all(n_hits != 40 for _term, n_hits in play.searches)
    assert ("Later Title", 20) not in play.searches


def test_random_word_skips_ignore_keywords_and_dead_queries():
    picked = choose_random_query(
        ["block", "laser"],
        ["block"],
        {"laser puzzle levels"},
        random.Random(0),
    )
    assert picked is None
    picked = choose_random_query(["block", "rope"], ["block"], set(), random.Random(0))
    assert picked == "rope puzzle levels"


def test_learn_prompt_rejects_exhausted_queries():
    profile = apply_profile(
        {"search_queries": ["old one"]},
        {"search_queries": ["Old One", "fresh rule puzzle"]},
        [],
        [],
        ["old one"],
    )
    assert profile["search_queries"] == ["fresh rule puzzle"]
    prompt = build_learn_prompt([], [{"package_id": "a", "score": 5}], {}, ["old one"])
    assert "Không dùng lại" in prompt
    assert "old one" in prompt


def test_remember_queries_keeps_the_first_spelling(tmp_path):
    updated = remember_queries({"exhausted": ["Stale Query"]}, ["stale query", "gravity puzzle levels"])
    assert updated["exhausted"] == ["Stale Query", "gravity puzzle levels"]
    store = Store(tmp_path)
    store.save_queries(updated)
    assert store.load_queries()["exhausted"] == ["Stale Query", "gravity puzzle levels"]


def test_search_word_file_avoids_ignore_keywords():
    root = Path(__file__).resolve().parents[1] / "data"
    words = Store(root).load_search_words()
    keywords = Store(root).load_ignore_keywords()
    tags = SteamData(root / "steam").load_ignore_tags()
    banned = [*keywords, *tags, "sokoban", "sudoku"]
    assert "gravity" in words
    assert "jump" in words
    assert "guide the herd" in words
    assert len(words) == len(set(words)) == 240
    assert all(not title_ignored(word, banned) for word in words)
