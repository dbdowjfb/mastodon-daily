from mastodon_daily.feed import make_item

BASE = {
    "id": "1",
    "author_acct": "a@b",
    "source": "home",
    "url": "https://original/post",
    "favourites": 0,
    "reblogs": 0,
    "created_at": "Wed, 14 Oct 2026 10:00:00 GMT",
    "media": [],
}


def test_identical_translation_is_dropped():
    it = make_item(dict(BASE, text="なんかかく", translation="なんかかく"), "r", 1)
    assert "（译）" not in it["description"]


def test_distinct_translation_is_kept():
    it = make_item(dict(BASE, text="猫が好き", translation="喜欢猫"), "r", 1)
    assert "（译）喜欢猫" in it["description"]


def test_link_points_at_feed_and_source_moved_to_text():
    it = make_item(dict(BASE, text="hi"), "r", 1, feed_link="https://app.example")
    assert it["link"] == "https://app.example"
    assert it["source_url"] == "https://original/post"
    assert 'href="https://original/post"' in it["description"]


def test_link_defaults_to_source_without_feed_link():
    it = make_item(dict(BASE, text="hi"), "r", 1)
    assert it["link"] == "https://original/post"
