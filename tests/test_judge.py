from mkresearch.judge import complete_picks, select_picks
from mkresearch.learn import anchor_ids


def test_select_picks_fills_five_when_asked():
    payload = {
        "picks": [
            {"app_id": "c", "has_levels": True, "novelty": 5, "mechanic_vi": "lạ"},
            {"app_id": "d", "has_levels": True, "novelty": 4, "mechanic_vi": "ổn"},
        ]
    }
    candidates = [
        {"appId": "c", "description": "Already picked."},
        {"appId": "d", "description": "Already picked."},
        {"appId": "h", "description": "Push boxes to the goal."},
        {"appId": "i", "description": "Reflect the beam."},
        {"appId": "j", "description": "Isolate the islands."},
    ]
    picks = complete_picks(select_picks(payload, {"c", "d", "h", "i", "j"}, limit=5), candidates, limit=5)
    assert [item["app_id"] for item in picks] == ["c", "d", "h", "i", "j"]


def test_select_picks_requires_levels_and_keeps_three():
    payload = {
        "picks": [
            {"app_id": "a", "has_levels": False, "novelty": 5},
            {"app_id": "b", "has_levels": True, "novelty": 2, "mechanic_vi": "yếu"},
            {"app_id": "c", "has_levels": True, "novelty": 5, "mechanic_vi": "lạ", "why_vi": "hay", "near_seed": "Cross Virus"},
            {"app_id": "d", "has_levels": True, "novelty": 4, "mechanic_vi": "ổn"},
            {"app_id": "e", "has_levels": True, "novelty": 3, "mechanic_vi": "được"},
            {"app_id": "f", "has_levels": True, "novelty": 5, "mechanic_vi": "ngoài danh sách"},
            {"app_id": "g", "has_levels": True, "novelty": 4, "mechanic_vi": "thêm"},
        ]
    }
    picks = select_picks(payload, {"a", "b", "c", "d", "e", "g"}, limit=3)
    assert [item["app_id"] for item in picks] == ["c", "d", "g"]


def test_report_skips_store_description_and_uses_first_screenshot():
    from mkresearch.telegram import first_screenshot, format_report

    app = {
        "title": "Snakebird",
        "appId": "com.example.game",
        "developer": "Noumenon Games",
        "installs": "100,000+",
        "score": 4.2,
        "description": "A very unique store description that should stay off Telegram.",
        "screenshots": ["https://play-lh.googleusercontent.com/shot-one", "https://play-lh.googleusercontent.com/shot-two"],
    }
    text = format_report(app, {"mechanic_vi": "Nuốt trái", "why_vi": "Luật lạ", "near_seed": "Lemmings"})
    assert "store description" not in text
    assert "https://play.google.com/store/apps/details?id=com.example.game&hl=en" in text
    assert first_screenshot(app) == "https://play-lh.googleusercontent.com/shot-one"
    assert first_screenshot({"screenshots": []}) is None


def test_anchors_keep_seeds_and_high_scores_only():
    seeds = [{"app_id": "jp.danball.crossvirus"}, {"app_id": "com.sadpuppy.lemmings"}]
    ratings = [
        {"package_id": "com.good.game", "score": 5},
        {"package_id": "com.bad.game", "score": 2},
        {"package_id": "com.good.game", "score": 4},
    ]
    assert anchor_ids(seeds, ratings) == [
        "jp.danball.crossvirus",
        "com.sadpuppy.lemmings",
        "com.good.game",
    ]
