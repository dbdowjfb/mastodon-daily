from mastodon_daily.main import load_config


def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_empty_env_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "")
    monkeypatch.delenv("BAR", raising=False)
    cfg = load_config(_write(tmp_path, "a: ${FOO,defA}\nb: ${BAR,defB}\n"))
    assert cfg["a"] == "defA"
    assert cfg["b"] == "defB"


def test_set_env_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "set")
    cfg = load_config(_write(tmp_path, "a: ${FOO,defA}\n"))
    assert cfg["a"] == "set"


def test_no_default_resolves_to_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("BAZ", raising=False)
    cfg = load_config(_write(tmp_path, "a: ${BAZ}\n"))
    assert cfg["a"] == ""
