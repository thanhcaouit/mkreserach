import json
import random
from pathlib import Path

from mkresearch.discover import (
    DetailLimit,
    choose_random_query,
    gather_passed,
    parse_play_suggestions,
    query_intent,
    suggestion_on_topic,
    recent_match_titles,
    remember_queries,
    remember_seen,
)
from mkresearch.filters import title_ignored
from mkresearch.learn import apply_profile, build_learn_prompt, refresh_queries
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
        self.hint_list: list[str] = []
        self.suggest_terms: list[str] = []

    def suggestions(self, term: str) -> list[str]:
        self.suggest_terms.append(term)
        return list(self.hint_list)

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
    passed, scanned, opened, _retired, _pending = gather_passed(
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
    passed, _scanned, opened, _retired, _pending = gather_passed(
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
    passed, _scanned, opened, _retired, _pending = gather_passed(
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
    passed, _scanned, opened, _retired, _pending = gather_passed(
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
    _passed, _scanned, opened, _retired, _pending = gather_passed(
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
    _passed, _scanned, opened, _retired, _pending = gather_passed(
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
    _passed, _scanned, opened, retired, _pending = gather_passed(
        play, ["anchor"], ["stale query"], {"apps": {}}, [], [], set(), {"old.id"}, []
    )
    assert opened == []
    assert retired == ["stale query"]
    assert ("stale query", 40) in play.searches

    play.searches.clear()
    _passed, _scanned, opened, retired_again, _pending = gather_passed(
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


def test_suggestions_fill_six_queries_and_batch_three_stays_off_catalog_titles():
    play = FakePlay()
    play.hint_list = [f"rule {index} maze" for index in range(12)]
    gather_passed(
        play,
        ["anchor"],
        ["profile query"],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        ["Old Title"],
        search_words=["gravity", "laser", "rope"],
        rng=random.Random(0),
        liked="tilt gravity",
    )
    first = [term for term, n_hits in play.searches if n_hits == 15]
    wide = [term for term, n_hits in play.searches if n_hits == 40]
    assert first[0] == "profile query"
    assert len(first) == 6
    assert all(not term.endswith(" puzzle levels") for term in first)
    assert wide[:6] == first
    assert len(wide) == 12
    assert wide[6:] == [f"rule {index} maze" for index in range(5, 11)]
    assert "Old Title" not in {term for term, _n_hits in play.searches}
    assert "gravity puzzle" in play.suggest_terms
    assert "gravity puzzle " in play.suggest_terms
    assert "gravity" not in play.suggest_terms


def test_suggestion_failure_falls_back_to_puzzle_levels():
    play = FakePlay()
    words = [f"word{index}" for index in range(8)]
    gather_passed(
        play,
        ["anchor"],
        [],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        ["Old Title"],
        search_words=words,
        rng=random.Random(0),
    )
    first = [term for term, n_hits in play.searches if n_hits == 15]
    assert len(first) == 6
    assert all(term.endswith(" puzzle levels") for term in first)
    assert "Old Title" not in {term for term, _n_hits in play.searches}


def test_same_intent_and_ignored_suggestions_are_skipped():
    assert query_intent("orbit puzzle levels") == query_intent("orbit")
    assert query_intent("laser puzzle") != query_intent("gravity puzzle")
    play = FakePlay()
    play.hint_list = ["orbit puzzle levels", "orbit", "block puzzle", "fresh maze"]
    _passed, _scanned, _opened, _retired, _pending = gather_passed(
        play,
        ["anchor"],
        [],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        [],
        keywords=["block"],
        search_words=["gravity"],
        rng=random.Random(0),
    )
    first = [term for term, n_hits in play.searches if n_hits == 15]
    assert "orbit puzzle levels" in first
    assert "orbit" not in first
    assert "block puzzle" not in first
    assert "fresh maze" in first


def test_unfinished_suggestion_is_pending_and_searched_first():
    play = FakePlay()
    play.hint_list = ["kept maze"]
    play.pages["kept maze"] = ["new.id"]
    play.details["new.id"] = _fail("new.id")
    _passed, _scanned, opened, retired, pending = gather_passed(
        play,
        ["anchor"],
        [],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        [],
        search_words=["gravity"],
        rng=random.Random(0),
    )
    assert opened == ["new.id"]
    assert "kept maze" not in retired
    assert pending[0] == "kept maze"

    play.searches.clear()
    play.hint_list = ["later maze"]
    gather_passed(
        play,
        ["anchor"],
        [],
        {"apps": {}},
        [],
        [],
        set(),
        {"new.id"},
        [],
        search_words=["gravity"],
        rng=random.Random(1),
        pending=pending,
    )
    assert [term for term, n_hits in play.searches if n_hits == 15][0] == "kept maze"


def test_off_topic_suggestions_are_dropped_and_not_pending():
    play = FakePlay()
    play.hint_list = ["hookup dating", "orbit maze", "stack sort"]
    _passed, _scanned, _opened, _retired, pending = gather_passed(
        play,
        ["anchor"],
        ["guide units indie"],
        {"apps": {}},
        [],
        [],
        set(),
        set(),
        [],
        search_words=["gravity"],
        rng=random.Random(0),
        pending=["stack sort", "orbit maze"],
    )
    searched = [term for term, _n_hits in play.searches]
    assert searched[0] == "orbit maze"
    assert "guide units indie" in searched
    assert "hookup dating" not in searched
    assert "stack sort" not in searched
    assert "stack sort" not in pending
    assert all(suggestion_on_topic(item) for item in pending)
    assert all(term.endswith("puzzle") or term.endswith("puzzle ") for term in play.suggest_terms)


def test_play_suggestion_payload_keeps_the_query_text():
    inner = [[
        ["orbit maze", None, [None, None, "/store/search?q=orbit+maze"]],
        ["orbit puzzle levels", None, [None, None, "/store/search?q=orbit"]],
    ]]
    frame = [["wrb.fr", "IJ4APc", json.dumps(inner), None, None, None, "generic"]]
    payload = ")]}'\n\n" + json.dumps(frame)
    assert parse_play_suggestions(payload) == ["orbit maze", "orbit puzzle levels"]


def test_five_games_do_not_retire_a_query_before_the_wide_search():
    play = FakePlay()
    hits = [f"p.{index}" for index in range(5)]
    play.similar["anchor"] = hits
    for app_id in hits:
        play.details[app_id] = _pass(app_id)
    play.pages["quiet query"] = ["old.id"]
    play.details["old.id"] = _fail("old.id")
    _passed, _scanned, _opened, retired, _pending = gather_passed(
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


class _RecordingLlm:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls = 0
        self.prompt = ""

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.prompt = prompt
        return self.raw


def test_refresh_writes_queries_when_profile_is_exhausted():
    llm = _RecordingLlm('{"search_queries":["fresh maze puzzle","old one"]}')
    profile = {
        "liked_mechanics": ["tilt gravity"],
        "disliked_mechanics": ["match-3"],
        "search_queries": ["old one"],
        "notes": "keep",
    }
    updated = refresh_queries(llm, [{"title": "Cross Virus"}], profile, ["old one"])
    assert llm.calls == 1
    assert "Play" in llm.prompt
    assert "old one" in llm.prompt
    assert updated["search_queries"] == ["fresh maze puzzle"]
    assert updated["liked_mechanics"] == ["tilt gravity"]
    assert updated["disliked_mechanics"] == ["match-3"]
    assert updated["notes"] == "keep"
    steam = refresh_queries(llm, [], profile, ["old one"], store_name="Steam")
    assert "Steam" in llm.prompt
    assert steam["search_queries"] == ["fresh maze puzzle"]


def test_refresh_treats_a_stripped_profile_query_as_exhausted():
    llm = _RecordingLlm('{"search_queries":["fresh maze puzzle","physics placement puzzle levels"]}')
    profile = {"search_queries": ["physics block placement puzzle levels"], "liked_mechanics": ["tilt"]}
    updated = refresh_queries(
        llm,
        [],
        profile,
        ["physics placement puzzle levels"],
        keywords=["block"],
    )
    assert llm.calls == 1
    assert updated["search_queries"] == ["fresh maze puzzle"]
    assert updated["liked_mechanics"] == ["tilt"]


def test_refresh_skips_when_a_query_is_still_alive():
    llm = _RecordingLlm('{"search_queries":["should not"]}')
    profile = {"search_queries": ["alive puzzle"], "liked_mechanics": ["tilt"]}
    updated = refresh_queries(llm, [], profile, ["dead one"])
    assert llm.calls == 0
    assert updated is profile


def test_refresh_keeps_profile_when_model_repeats_dead_queries():
    llm = _RecordingLlm('{"search_queries":["old one"]}')
    profile = {"search_queries": ["old one"], "liked_mechanics": ["tilt"]}
    updated = refresh_queries(llm, [], profile, ["old one"])
    assert llm.calls == 1
    assert updated is profile


def test_refresh_runs_when_queries_are_empty():
    llm = _RecordingLlm('{"search_queries":["grid maze puzzle"]}')
    profile = {"search_queries": [], "liked_mechanics": ["isolate"]}
    updated = refresh_queries(llm, [], profile, [])
    assert updated["search_queries"] == ["grid maze puzzle"]
    assert updated["liked_mechanics"] == ["isolate"]


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
    updated = remember_queries({"exhausted": ["Stale Query"]}, ["stale query", "gravity puzzle levels"], ["fresh query", "stale query"])
    assert updated["exhausted"] == ["Stale Query", "gravity puzzle levels"]
    assert updated["pending"] == ["fresh query"]
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
