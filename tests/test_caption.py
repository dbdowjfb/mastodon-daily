import json
import sys
import types

import pytest

from mastodon_daily import ranker


class _Resp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _obj(captions):
    return json.dumps({"captions": captions}, ensure_ascii=False)


def _fake_st(np):
    mod = types.ModuleType("sentence_transformers")

    class ST:
        def __init__(self, *a, **k):
            pass

        def encode(self, texts, **kw):
            out = []
            for t in texts:
                v = np.array([t.count("cat") + t.count("猫"), 1.0], float)
                out.append(v / (np.linalg.norm(v) or 1.0))
            return np.array(out)

    setattr(mod, "SentenceTransformer", ST)
    return mod


def test_no_api_key_is_noop():
    items = [{"media": ["http://x/1.jpg"]}]
    assert ranker.caption_items({"enabled": True, "api_key": ""}, items) == 0
    assert "_alt" not in items[0]


def test_captions_are_attached(monkeypatch):
    monkeypatch.setattr(ranker, "_data_url", lambda u, m: "data:image/jpeg;base64,AAA")
    seen = {}

    def post(url, **kwargs):
        seen["content"] = kwargs["json"]["messages"][0]["content"]
        return _Resp(_obj(["一只猫", "一条狗"]))

    monkeypatch.setattr(ranker.requests, "post", post)
    items = [{"media": ["http://x/1.jpg"]}, {"media": ["http://x/2.jpg"]}, {"media": []}]
    n = ranker.caption_items({"enabled": True, "api_key": "k", "model": "m", "batch": 6}, items)
    assert n == 2
    assert items[0]["_alt"] == "一只猫"
    assert items[1]["_alt"] == "一条狗"
    assert seen["content"][0]["type"] == "text"
    assert seen["content"][1]["type"] == "image_url"


def test_caption_parse_failure_is_tolerated(monkeypatch):
    monkeypatch.setattr(ranker, "_data_url", lambda u, m: "data:x")
    monkeypatch.setattr(ranker.requests, "post", lambda url, **k: _Resp("no json here"))
    items = [{"media": ["http://x/1.jpg"]}]
    assert ranker.caption_items({"enabled": True, "api_key": "k"}, items) == 0
    assert "_alt" not in items[0]


def test_image_fetch_failure_is_tolerated(monkeypatch):
    def boom(u, m):
        raise RuntimeError("nope")

    monkeypatch.setattr(ranker, "_data_url", boom)
    items = [{"media": ["http://x/1.jpg"]}]
    assert ranker.caption_items({"enabled": True, "api_key": "k"}, items) == 0


def test_resolve_caption_cfg_falls_back_to_llm():
    vcfg = ranker.resolve_caption_cfg(
        {"llm": {"base_url": "https://api.x", "api_key": "K", "model": "m1"}, "caption": {}}
    )
    assert vcfg["enabled"] is True
    assert vcfg["base_url"] == "https://api.x"
    assert vcfg["api_key"] == "K"
    assert vcfg["model"] == "m1"


def test_resolve_caption_cfg_disabled():
    assert ranker.resolve_caption_cfg({"caption": {"enabled": False}}) == {"enabled": False}


def test_candidate_text_includes_alt():
    t = ranker._candidate_text({"text": "hi", "tags": ["a"], "_alt": "一只猫"})
    assert "hi" in t and "a" in t and "一只猫" in t


def test_alt_is_used_in_text_ranking(monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_st(np))
    profile = [{"text": "cat"}]
    cands = [
        {"text": "hello", "tags": [], "media": [], "_alt": "一只猫"},
        {"text": "world", "tags": [], "media": [], "_alt": "风景照片"},
    ]
    picks = ranker.rank({}, profile, cands, 2, {"enabled": True, "model": "fake"})
    assert picks[0]["index"] == 0
