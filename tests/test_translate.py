import json

from mastodon_daily import ranker


class _Resp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _obj(translations):
    return "```json\n" + json.dumps({"translations": translations}, ensure_ascii=False) + "\n```"


def _patch(monkeypatch, handler):
    def post(url, **kwargs):
        return handler(kwargs["json"]["messages"][0]["content"])

    monkeypatch.setattr(ranker.requests, "post", post)


def test_no_api_key_passthrough():
    texts = ["a", "b"]
    assert ranker.translate_to_zh({}, texts) == texts
    assert ranker.translate_to_zh({"api_key": ""}, texts) == texts


def test_retry_fixes_empty_and_echoed(monkeypatch):
    texts = ["猫が好き", "This is how you relax, right? #cat", "🦆🦆", "RE: https://x/y"]

    def handler(prompt):
        if "严禁原样照抄" in prompt:
            return _Resp(_obj(["喜欢猫", "这是你放松的方式，对吧？"]))
        return _Resp(_obj(["", texts[1], texts[2]]))  # empty + echoed

    _patch(monkeypatch, handler)
    out = ranker.translate_to_zh({"api_key": "k"}, texts)
    assert out[0] == "喜欢猫"
    assert out[1] == "这是你放松的方式，对吧？"
    assert out[2] == "🦆🦆"  # no word chars -> left as-is
    assert out[3] == texts[3]  # url only -> left as-is


def test_unparsable_response_returns_originals(monkeypatch):
    texts = ["こんにちは"]
    _patch(monkeypatch, lambda prompt: _Resp("sorry, no json here"))
    assert ranker.translate_to_zh({"api_key": "k"}, texts) == texts


def test_http_error_returns_originals(monkeypatch):
    texts = ["こんにちは"]

    def post(url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ranker.requests, "post", post)
    assert ranker.translate_to_zh({"api_key": "k"}, texts) == texts


def test_chunks_large_batches(monkeypatch):
    texts = [f"text{i}" for i in range(20)]
    calls = {"n": 0}

    def handler(prompt):
        calls["n"] += 1
        return _Resp(_obj([]))  # nothing translated -> originals

    _patch(monkeypatch, handler)
    out = ranker.translate_to_zh({"api_key": "k"}, texts)
    assert out == texts
    assert calls["n"] >= 2  # 16 + 4 chunks (plus retry pass)
