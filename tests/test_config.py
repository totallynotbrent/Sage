from __future__ import annotations

from app.config import PLACEHOLDER_KEY, Settings, get_settings, validation_problems


def test_defaults():
    settings = Settings(_env_file=None)
    assert settings.api_url == "https://ollama.com/v1"
    assert settings.api_key == ""
    assert settings.model == ""
    assert settings.searxng_url == ""
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.max_upload_mb == 30
    assert settings.max_total_mb == 500
    assert settings.chunk_chars == 1500
    assert settings.chunk_overlap == 200
    assert settings.context_chunk_budget == 8
    assert settings.watch_dirs == []
    assert settings.watch_scan_seconds == 300
    assert settings.data_dir.name == "sage"


def test_db_and_uploads_paths(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    assert settings.db_path == tmp_path / "data" / "sage.db"
    assert settings.uploads_dir == tmp_path / "data" / "uploads"


def test_ollama_url_appends_v1():
    settings = Settings(api_url="https://ollama.com", _env_file=None)
    assert settings.api_url == "https://ollama.com/v1"


def test_missing_key_problem():
    settings = Settings(api_key="", _env_file=None)
    problems = validation_problems(settings)
    assert any("API_KEY" in p for p in problems)


def test_placeholder_key_problem():
    settings = Settings(api_key=PLACEHOLDER_KEY, _env_file=None)
    problems = validation_problems(settings)
    assert any("placeholder" in p for p in problems)


def test_bad_url_problem():
    settings = Settings(api_key="k", api_url="not-a-url", _env_file=None)
    problems = validation_problems(settings)
    assert any("not a valid" in p for p in problems)


def test_good_config_has_no_problems():
    settings = Settings(
        api_key="some-key",
        api_url="http://127.0.0.1:8877/v1",
        model="some-model",
        _env_file=None,
    )
    assert validation_problems(settings) == []


def test_empty_model_is_a_problem():
    # an unset MODEL must be caught at startup: it would otherwise surface as
    # a pydantic ChatRequest ValidationError on every chat turn
    settings = Settings(api_key="some-key", api_url="http://127.0.0.1:8877/v1", _env_file=None)
    problems = validation_problems(settings)
    assert any("MODEL is empty" in p for p in problems)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("API_URL", "http://example.com:1234/v1")
    monkeypatch.setenv("MODEL", "some-model")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.api_url == "http://example.com:1234/v1"
        assert settings.model == "some-model"
        assert settings.host == "127.0.0.1"
        assert settings.port == 9000
    finally:
        get_settings.cache_clear()


def test_watch_dirs_env_parse(monkeypatch):
    monkeypatch.setenv("SAGE_WATCH_DIRS", '["/notes/calculus", "/notes/algebra"]')
    monkeypatch.setenv("SAGE_WATCH_SCAN_SECONDS", "60")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.watch_dirs == ["/notes/calculus", "/notes/algebra"]
        assert settings.watch_scan_seconds == 60
    finally:
        get_settings.cache_clear()