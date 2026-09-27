from mkresearch.judge import select_picks
from mkresearch.learn import anchor_ids


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
