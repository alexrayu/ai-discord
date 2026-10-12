from unittest.mock import MagicMock

import discord
import pytest

from bot import is_chat_message, is_owner
from config import load_config

OWNER, CHANNEL = 111, 222
ENV = {"DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": str(CHANNEL), "ANTHROPIC_API_KEY": "k", "CLAUDE_MODEL": "m",
       "OPENAI_API_KEY": "o", "GPT_MODEL": "g"}
CONFIG = load_config({**ENV, "OWNER_USER_ID": str(OWNER)})


def message(author_id=OWNER, bot=False, webhook_id=None, channel=None):
    msg = MagicMock()
    msg.author.id, msg.author.bot, msg.webhook_id = author_id, bot, webhook_id
    if channel is None:
        channel = MagicMock(spec=discord.TextChannel, id=CHANNEL)
    msg.channel = channel
    return msg


@pytest.mark.parametrize("author_id", [OWNER, 333], ids=["owner", "member"])
def test_human_in_allowed_channel(author_id):
    assert is_chat_message(message(author_id=author_id), CONFIG)


def test_human_in_thread_of_allowed_channel():
    thread = MagicMock(spec=discord.Thread, id=999, parent_id=CHANNEL)
    assert is_chat_message(message(channel=thread), CONFIG)


@pytest.mark.parametrize(
    "msg",
    [
        message(webhook_id=12345),
        message(webhook_id=12345, author_id=OWNER),
        message(bot=True),
        message(channel=MagicMock(spec=discord.TextChannel, id=444)),
        message(channel=MagicMock(spec=discord.Thread, id=999, parent_id=444)),
        message(channel=MagicMock(spec=discord.DMChannel, id=555)),
    ],
    ids=["webhook", "webhook-owner-id", "bot", "other-channel", "other-thread", "dm"],
)
def test_ignored(msg):
    assert not is_chat_message(msg, CONFIG)


def test_owner_explicit():
    assert is_owner(message(author_id=OWNER), CONFIG)
    assert not is_owner(message(author_id=333), CONFIG)


def test_owner_defaults_to_server_owner():
    config = load_config(ENV)
    msg = message(author_id=333)
    msg.guild.owner_id = 333
    assert is_owner(msg, config)
    msg.guild.owner_id = 444
    assert not is_owner(msg, config)


def test_no_owner_in_dm_without_explicit_owner():
    msg = message(author_id=333)
    msg.guild = None
    assert not is_owner(msg, load_config(ENV))
