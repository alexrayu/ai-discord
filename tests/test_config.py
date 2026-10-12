import pytest

from config import ConfigError, load_config

VALID = {
    "DISCORD_TOKEN": "token",
    "ALLOWED_CHANNEL_IDS": "222, 333",
    "ANTHROPIC_API_KEY": "key",
    "CLAUDE_MODEL": "claude-sonnet-5-5",
}


def test_valid():
    config = load_config(VALID)
    assert config.owner_user_id is None
    assert config.allowed_channel_ids == {222, 333}
    assert config.debug is False
    assert (config.claude_effort, config.max_reply_tokens, config.user_rounds_per_hour) == ("low", 4000, 20)


@pytest.mark.parametrize("key", list(VALID))
def test_missing_required(key):
    with pytest.raises(ConfigError, match=key):
        load_config({k: v for k, v in VALID.items() if k != key})


def test_explicit_owner():
    assert load_config({**VALID, "OWNER_USER_ID": "111"}).owner_user_id == 111


def test_non_numeric_id():
    with pytest.raises(ConfigError, match="OWNER_USER_ID"):
        load_config({**VALID, "OWNER_USER_ID": "abc"})


def test_all_errors_reported_at_once():
    with pytest.raises(ConfigError) as e:
        load_config({})
    assert all(key in str(e.value) for key in VALID)


@pytest.mark.parametrize("key, value", [("MAX_REPLY_TOKENS", "0"), ("USER_ROUNDS_PER_HOUR", "x"), ("CLAUDE_EFFORT", "turbo")])
def test_invalid_values(key, value):
    with pytest.raises(ConfigError, match=key):
        load_config({**VALID, key: value})
