import sys
import types

import pytest

from mastodon_daily import ranker


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


def test_heuristic_fallback_when_st_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    cands = [
        {"text": "hello", "media": [], "author_acct": "a", "tags": []},
        {"text": "猫 cat", "media": [], "author_acct": "b", "tags": []},
    ]
    picks = ranker.rank({}, [{"text": "cat"}], cands, 1, {"enabled": True, "model": "x"})
    assert picks[0]["reason"] in ("偏好匹配（图/猫相关）", "多样性推荐")


def test_disabled_uses_heuristic():
    cands = [{"text": "猫", "media": [], "author_acct": "a", "tags": []}]
    picks = ranker.rank({}, [{"text": "cat"}], cands, 1, {"enabled": False})
    assert picks and picks[0]["index"] == 0


def test_embeddings_rank_and_diversity_fill(monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_st(np))

    profile = [{"text": "猫 cat"}]
    cands = [
        {"text": "bread recipe", "tags": [], "media": []},
        {"text": "猫が好き", "tags": ["猫"], "media": []},
        {"text": "", "tags": [], "media": ["http://x/1.jpg"]},  # media only
    ]
    picks = ranker.rank({}, profile, cands, 3, {"enabled": True, "model": "fake"})
    assert picks[0]["index"] == 1
    assert picks[0]["reason"].startswith("画像相似度")
    assert picks[-1]["index"] == 2 and picks[-1]["reason"] == "多样性补位"


def test_image_and_text_combined(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")

    cat = tmp_path / "cat.png"
    dog = tmp_path / "dog.png"
    Image.new("RGB", (100, 50)).save(cat)
    Image.new("RGB", (50, 100)).save(dog)

    mod = types.ModuleType("sentence_transformers")

    class ST:
        def __init__(self, *a, **k):
            pass

        def encode(self, items, **kw):
            out = []
            for x in items:
                if isinstance(x, str):  # text
                    v = np.array([x.count("cat") + x.count("猫"), 1.0], float)
                else:  # PIL image -> [w, h]
                    w, h = x.size
                    v = np.array([w, h], float)
                out.append(v / (np.linalg.norm(v) or 1.0))
            return np.array(out)

    setattr(mod, "SentenceTransformer", ST)
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)

    profile = [{"text": "cat", "_image_paths": [str(cat)]}]
    cands = [
        {"text": "", "tags": [], "media": [], "_image_paths": [str(cat)]},   # image matches
        {"text": "", "tags": [], "media": [], "_image_paths": [str(dog)]},   # image differs
        {"text": "hello cat", "tags": [], "media": [], "_image_paths": []},  # text only
    ]
    cfg = {"enabled": True, "model": "fake", "image": {"enabled": True, "model": "fake-img", "weight": 0.5}}
    picks = ranker.rank({}, profile, cands, 3, cfg)
    assert picks[0]["index"] == 0
    assert "（图）" in picks[0]["reason"]
