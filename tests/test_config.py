import pytest

from bot.config import load_config


def test_roles_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_IDS", "1, 2")
    monkeypatch.setenv("OPERATOR_IDS", "2, 3")
    monkeypatch.setenv("ADMIN_USERNAMES", "@Maxm_dm, other")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.delenv("APPLICATIONS_CHAT_ID", raising=False)
    monkeypatch.delenv("RESPONSE_TIME", raising=False)
    config = load_config()
    assert config.role_of(1) == "admin"
    assert config.role_of(2) == "admin"
    assert config.role_of(3) == "operator"
    assert config.role_of(9) is None
    assert config.role_of(9, "maxm_dm") == "admin"
    assert config.role_of(9, "@Maxm_dm") == "admin"
    assert config.role_of(3, "maxm_dm") == "admin"
    assert config.applications_chat_id is None
    assert config.response_time == "рабочего дня"
    assert config.state_ttl_seconds == 24 * 60 * 60


def test_token_is_required(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "  ")
    with pytest.raises(SystemExit):
        load_config()


def test_bad_admin_id_stops_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_IDS", "1,abc")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bot.sqlite"))
    with pytest.raises(SystemExit):
        load_config()
