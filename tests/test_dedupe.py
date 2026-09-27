from mkresearch.dedupe import is_duplicate, normalize_title


SEEDS = [
    {"app_id": "jp.danball.crossvirus", "title": "Cross Virus"},
    {"app_id": "com.sadpuppy.lemmings", "title": "Lemmings: Puzzle Survival"},
]


def test_same_package_or_normalized_title_is_duplicate():
    catalog = {
        "apps": {
            "com.example.one": {"title": "Odd Boxes!", "title_norm": "odd boxes"},
        }
    }
    assert normalize_title("Cross  Virus!") == "cross virus"
    assert is_duplicate("com.example.one", "Other", catalog, SEEDS)
    assert is_duplicate("com.example.other", "Odd Boxes", catalog, SEEDS)
    assert is_duplicate("jp.danball.crossvirus", "Cross Virus", catalog, SEEDS)
    assert is_duplicate("com.repack.virus", "Cross Virus", catalog, SEEDS)
    assert not is_duplicate("com.example.fresh", "Fresh Rule", catalog, SEEDS)
