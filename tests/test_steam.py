import random
from pathlib import Path

from mkresearch.steam.__main__ import gather_passed
from mkresearch.steam.discover import (
    DetailLimit,
    app_id_from_logo,
    merge_blocklist,
    parse_search_items,
    parse_store_tags,
    remember_seen,
)
from mkresearch.steam.filters import hard_reject, in_review_band
from mkresearch.steam.judge import complete_picks, select_picks
from mkresearch.steam.report import format_report
from mkresearch.steam.store import SteamData, already_sent

IGNORED_TAGS = [
    "Platformer",
    "First-Person",
    "Action",
    "Action-Adventure",
    "Shooter",
    "Horror",
    "FPS",
    "Gore",
    "Detective",
    "Investigation",
    "Combat",
    "Violent",
    "Sexual Content",
    "Racing",
    "Nudity",
    "Romance",
    "Hack and Slash",
    "VR",
    "Idle",
    "Historical",
    "Demons",
]


def _app(**extra):
    base = {
        "appId": "10",
        "title": "Can of Wormholes",
        "developer": "Solo",
        "publishers": ["Solo"],
        "reviews": 200,
        "type": "game",
        "coming_soon": False,
        "from_puzzle_tag": True,
        "genres": ["Indie"],
    }
    base.update(extra)
    return base


def test_review_band_edges():
    assert in_review_band(0)
    assert in_review_band(20_000)
    assert not in_review_band(20_001)
    assert in_review_band(None)


def test_hard_reject_reasons():
    assert hard_reject(_app(type="dlc"), [], set()) == "type"
    assert hard_reject(_app(coming_soon=True), [], set()) == "unreleased"
    assert hard_reject(_app(reviews=20_001), [], set()) == "reviews"
    assert hard_reject(_app(reviews=None), [], set()) is None
    assert hard_reject(_app(developer="Valve"), ["Valve"], set()) == "publisher"
    assert hard_reject(_app(publishers=["2K Games"]), ["2K"], set()) == "publisher"
    assert hard_reject(_app(), [], {"10"}) == "chart"
    assert hard_reject(_app(from_puzzle_tag=False, genres=["Indie"]), [], set()) == "genre"
    assert hard_reject(_app(), ["Valve"], set()) is None


class FakeSteam:
    def __init__(self, cap: int = 2) -> None:
        self.detail_calls = 0
        self.cap = cap
        self.halted = False
        self.pages: dict[str, list[str]] = {}
        self.details: dict[str, dict] = {}
        self.opened: list[str] = []
        self.searches: list[str] = []

    def begin_batch(self) -> None:
        self.detail_calls = 0

    def search_hits(self, queries: list[str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for query in queries:
            self.searches.append(query)
            found[query] = list(self.pages.get(query, []))
        return found

    def search_ids(self, queries: list[str]) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for ids in self.search_hits(queries).values():
            for app_id in ids:
                if app_id not in seen:
                    seen.add(app_id)
                    found.append(app_id)
        return found

    def app_details(self, app_id: str) -> dict:
        if self.detail_calls >= self.cap:
            raise DetailLimit("cap")
        self.detail_calls += 1
        self.opened.append(app_id)
        return dict(self.details[app_id])


def _steam_catalog(title: str) -> dict:
    return {"apps": {"1": {"title": title, "suggested_at": "2026-09-27T00:00:00Z"}}}


def test_one_steam_passer_keeps_searching_until_five():
    client = FakeSteam()
    client.pages["q"] = ["a", "b", "c"]
    client.details["a"] = _app(appId="a", reviews=20_001)
    client.details["b"] = _app(appId="b", reviews=20_001)
    client.details["c"] = _app(appId="c", title="Fresh Puzzle")
    client.pages["Newest"] = ["later"]
    client.details["later"] = _app(appId="later", reviews=20_001)
    passed, scanned, opened, _retired = gather_passed(
        client, ["q"], _steam_catalog("Newest"), [], [], set(), {"apps": {}}, [], set(), ["Newest"]
    )
    assert [app["appId"] for app in passed] == ["c"]
    assert scanned == 4
    assert opened == ["a", "b", "c", "later"]
    assert "Newest" in client.searches


def test_three_steam_batches_when_nothing_passes():
    client = FakeSteam()
    client.pages["q"] = ["a", "b", "c", "d"]
    client.pages["Newest"] = ["e"]
    for app_id in ("a", "b", "c", "d", "e"):
        client.details[app_id] = _app(appId=app_id, reviews=20_001)
    passed, _scanned, opened, _retired = gather_passed(
        client, ["q"], _steam_catalog("Newest"), [], [], set(), {"apps": {}}, [], set(), ["Newest"]
    )
    assert passed == []
    assert opened == ["a", "b", "c", "d", "e"]
    assert client.searches.count("Newest") == 1


def test_seen_steam_ids_are_not_opened_again():
    client = FakeSteam(cap=20)
    client.pages["q"] = ["old", "new"]
    client.details["old"] = _app(appId="old")
    client.details["new"] = _app(appId="new", reviews=20_001)
    _passed, _scanned, opened, _retired = gather_passed(
        client, ["q"], {"apps": {}}, [], [], set(), {"apps": {}}, [], {"old"}, []
    )
    assert opened == ["new"]
    seen = remember_seen({"apps": {}}, opened, "2026-09-29")
    assert seen["apps"]["new"]["seen"] == "2026-09-29"


def test_five_steam_games_stop_the_later_batches():
    client = FakeSteam(cap=20)
    hits = [f"p.{index}" for index in range(6)]
    client.pages["q"] = hits
    for app_id in hits:
        client.details[app_id] = _app(appId=app_id)
    client.pages["Newest"] = ["later"]
    client.details["later"] = _app(appId="later")
    passed, _scanned, opened, retired = gather_passed(
        client, ["q"], _steam_catalog("Newest"), [], [], set(), {"apps": {}}, [], set(), ["Newest"]
    )
    assert [app["appId"] for app in passed] == hits
    assert "later" not in opened
    assert "Newest" not in client.searches
    assert retired == []


def test_exhausted_steam_query_is_not_searched_again():
    client = FakeSteam(cap=20)
    client.pages["stale query"] = ["old"]
    client.details["old"] = _app(appId="old")
    _passed, _scanned, opened, retired = gather_passed(
        client, ["stale query"], {"apps": {}}, [], [], set(), {"apps": {}}, [], {"old"}, []
    )
    assert opened == []
    assert retired == ["stale query"]
    client.searches.clear()
    _passed, _scanned, opened, retired_again = gather_passed(
        client,
        ["stale query"],
        {"apps": {}},
        [],
        [],
        set(),
        {"apps": {}},
        [],
        {"old"},
        [],
        exhausted=retired,
    )
    assert opened == []
    assert retired_again == []
    assert "stale query" not in client.searches


def test_each_steam_run_searches_one_play_word():
    client = FakeSteam(cap=20)
    words = ["gravity", "laser", "rope"]
    expected = random.Random(0).choice([f"{word} puzzle levels" for word in words])
    _passed, _scanned, _opened, _retired = gather_passed(
        client,
        ["profile query"],
        {"apps": {}},
        [],
        [],
        set(),
        {"apps": {}},
        [],
        set(),
        [],
        search_words=words,
        rng=random.Random(0),
    )
    extra = {term for term in client.searches if term.endswith(" puzzle levels")}
    assert extra == {expected}
    assert client.searches.count("profile query") == 2


def test_ignored_tags_drop_action_views_and_keep_adventure():
    assert hard_reject(_app(genres=["Action", "Indie"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(genres=["Action RPG"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(tags=["Precision Platformer"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(tags=["First-Person Shooter"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(tags=["Third-Person Shooter"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(tags=["Survival Horror"]), [], set(), IGNORED_TAGS) == "tag"
    assert hard_reject(_app(tags=["Puzzle"], genres=["Indie"]), [], set(), IGNORED_TAGS) is None
    assert hard_reject(_app(genres=["Adventure", "Indie"]), [], set(), IGNORED_TAGS) is None


def test_store_page_tags_skip_the_add_button():
    html = """
    <a class="app_tag" href="/tags/en/Puzzle"> Puzzle </a>
    <a class="app_tag" href="/tags/en/Platformer">Platformer</a>
    <div class="app_tag add_button">+</div>
    """
    assert parse_store_tags(html) == ["Puzzle", "Platformer"]


def test_ignore_tag_file_lists_the_starter_tags():
    tags = SteamData(Path(__file__).resolve().parents[1] / "data" / "steam").load_ignore_tags()
    assert tags == IGNORED_TAGS
    assert "Adventure" not in tags


def test_logo_and_search_items_use_puzzle_tag():
    logo = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/357300/capsule_sm_120.jpg"
    assert app_id_from_logo(logo) == "357300"
    items = parse_search_items({"items": [{"name": "Snakebird", "logo": logo}]})
    assert items == [{"appId": "357300", "title": "Snakebird", "from_puzzle_tag": True}]


def test_blocklist_records_chart_ids():
    merged = merge_blocklist({"apps": {}}, ["1057090"], "2026-09-27")
    assert merged["apps"]["1057090"]["seen"] == "2026-09-27"


def test_skips_play_title_and_steam_seed():
    play = {"apps": {"com.example": {"title": "Snakebird", "title_norm": "snakebird"}}}
    assert already_sent("999", "Snakebird", {"apps": {}}, [], play, [])
    assert already_sent("357300", "Other", {"apps": {}}, [{"app_id": "357300", "title": "Snakebird"}], {"apps": {}}, [])
    assert not already_sent("42", "Can of Wormholes", {"apps": {}}, [], {"apps": {}}, [])


def test_report_is_steam_link_without_description():
    text = format_report(
        {
            "title": "Can of Wormholes",
            "appId": "1331680",
            "developer": "Someone",
            "reviews": 400,
            "is_free": False,
            "price_text": "$9.99",
            "description": "A very unique store description that should stay off Telegram.",
        },
        {"near_seed": "Snakebird", "mechanic_vi": "Nuốt trái"},
    )
    assert text.startswith("Steam\n")
    assert "https://store.steampowered.com/app/1331680" in text
    assert "store description" not in text
    assert "Nuốt trái" not in text
    assert "Cơ chế" not in text
    assert "Vì sao" not in text


def test_select_picks_fills_five():
    payload = {
        "picks": [
            {"app_id": "1", "has_levels": True, "novelty": 5, "near_seed": "Snakebird"},
            {"app_id": "2", "has_levels": "true", "novelty": "4"},
        ]
    }
    candidates = [{"appId": str(i), "title": f"Game {i}"} for i in range(1, 8)]
    picks = complete_picks(select_picks(payload, {c["appId"] for c in candidates}), candidates)
    assert [item["app_id"] for item in picks] == ["1", "2", "3", "4", "5"]


def test_research_workflow_keeps_play_job_and_commits_only_steam_data():
    text = open(".github/workflows/research.yml", encoding="utf-8").read()
    assert "python -m mkresearch.main ${{ github.event.inputs.mode || 'research' }}" in text
    assert "python -m mkresearch.steam research" in text
    assert "git add data/steam" in text
    play_job, steam_job = text.split("\n  steam:", 1)
    assert "git add data/steam" not in play_job
    assert "needs: [gate, run]" in steam_job
    assert "source" in text and "backup" in text
    assert "python -m mkresearch.main" in play_job
