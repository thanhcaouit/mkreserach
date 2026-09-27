from mkresearch.telegram import collect_ratings, parse_score_reply


def test_parse_score_reply():
    assert parse_score_reply("4") == (4, "")
    assert parse_score_reply("5 gameplay lạ") == (5, "gameplay lạ")
    assert parse_score_reply("0") is None
    assert parse_score_reply("6") is None
    assert parse_score_reply("10") is None
    assert parse_score_reply("hello") is None


def test_collect_ratings_ignores_non_replies_and_bad_scores():
    updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 20,
                "text": "4",
                "chat": {"id": 9},
                "reply_to_message": {"message_id": 5},
            },
        },
        {
            "update_id": 2,
            "message": {
                "message_id": 21,
                "text": "5 gameplay lạ",
                "chat": {"id": 9},
                "reply_to_message": {"message_id": 6},
            },
        },
        {
            "update_id": 3,
            "message": {
                "message_id": 22,
                "text": "0",
                "chat": {"id": 9},
                "reply_to_message": {"message_id": 5},
            },
        },
        {
            "update_id": 4,
            "message": {
                "message_id": 23,
                "text": "6",
                "chat": {"id": 9},
                "reply_to_message": {"message_id": 5},
            },
        },
        {
            "update_id": 5,
            "message": {
                "message_id": 24,
                "text": "4",
                "chat": {"id": 9},
            },
        },
        {
            "update_id": 6,
            "message": {
                "message_id": 25,
                "text": "4",
                "chat": {"id": 99},
                "reply_to_message": {"message_id": 5},
            },
        },
    ]
    ratings, offset = collect_ratings(
        updates,
        "9",
        {5: "com.example.one", 6: "com.example.two"},
    )
    assert offset == 7
    assert [(item["package_id"], item["score"], item["note"]) for item in ratings] == [
        ("com.example.one", 4, ""),
        ("com.example.two", 5, "gameplay lạ"),
    ]
