import pytest

from config import ConfigError, load_config

VALID = {
    "DISCORD_TOKEN": "token",
    "OWNER_USER_ID": "111",
    "ALLOWED_CHANNEL_IDS": "222, 333",
}


def test_valid():
    config = load_config(VALID)
    assert config.owner_user_id == 111
    assert config.allowed_channel_ids == {222, 333}
    assert config.claude_avatar_url is None
    assert config.debug is False


@pytest.mark.parametrize("key", list(VALID))
def test_missing_required(key):
    with pytest.raises(ConfigError, match=key):
        load_config({k: v for k, v in VALID.items() if k != key})


def test_non_numeric_id():
    with pytest.raises(ConfigError, match="OWNER_USER_ID"):
        load_config({**VALID, "OWNER_USER_ID": "abc"})


def test_avatar_must_be_https():
    with pytest.raises(ConfigError, match="CLAUDE_AVATAR_URL"):
        load_config({**VALID, "CLAUDE_AVATAR_URL": "/tmp/a.png"})


def test_all_errors_reported_at_once():
    with pytest.raises(ConfigError) as e:
        load_config({})
    assert all(key in str(e.value) for key in VALID)
