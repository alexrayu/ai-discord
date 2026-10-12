from unittest.mock import MagicMock

import discord
import pytest

from bot import is_owner_message
from config import Config

OWNER, CHANNEL = 111, 222
CONFIG = Config(
    discord_token="t",
    owner_user_id=OWNER,
    allowed_channel_ids=frozenset({CHANNEL}),
    claude_avatar_url=None,
    gpt_avatar_url=None,
    debug=False,
)


def message(author_id=OWNER, bot=False, webhook_id=None, channel=None):
    msg = MagicMock()
    msg.author.id, msg.author.bot, msg.webhook_id = author_id, bot, webhook_id
    if channel is None:
        channel = MagicMock(spec=discord.TextChannel, id=CHANNEL)
    msg.channel = channel
    return msg


def test_owner_in_allowed_channel():
    assert is_owner_message(message(), CONFIG)


def test_owner_in_thread_of_allowed_channel():
    thread = MagicMock(spec=discord.Thread, id=999, parent_id=CHANNEL)
    assert is_owner_message(message(channel=thread), CONFIG)


@pytest.mark.parametrize(
    "msg",
    [
        message(webhook_id=12345),
        message(webhook_id=12345, author_id=OWNER),
        message(bot=True),
        message(author_id=333),
        message(channel=MagicMock(spec=discord.TextChannel, id=444)),
        message(channel=MagicMock(spec=discord.Thread, id=999, parent_id=444)),
        message(channel=MagicMock(spec=discord.DMChannel, id=555)),
    ],
    ids=["webhook", "webhook-owner-id", "bot", "other-user", "other-channel", "other-thread", "dm"],
)
def test_ignored(msg):
    assert not is_owner_message(msg, CONFIG)
