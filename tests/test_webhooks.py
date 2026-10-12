from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from webhooks import MESSAGE_LIMIT, Webhooks, split_message

BOT_ID = 42


# Splitting

def test_short_message_untouched():
    assert split_message("hi") == ["hi"]


def test_parts_within_limit_and_prefer_paragraphs():
    text = "\n\n".join(["a" * 900] * 5)
    parts = split_message(text)
    assert all(len(p) <= MESSAGE_LIMIT for p in parts)
    assert all(para == "a" * 900 for p in parts for para in p.split("\n\n"))
    assert sum(p.count("a") for p in parts) == 4500


def test_hard_cut_without_newlines():
    parts = split_message("x" * 5000)
    assert all(len(p) <= MESSAGE_LIMIT for p in parts)
    assert "".join(parts) == "x" * 5000


def test_code_fence_closed_and_reopened():
    code = "\n".join(f"line_{i} = {i}" for i in range(300))
    parts = split_message(f"Look:\n```python\n{code}\n```\nDone.")
    assert len(parts) > 1
    assert all(len(p) <= MESSAGE_LIMIT for p in parts)
    for p in parts:
        assert p.count("```") % 2 == 0
    for p in parts[1:]:
        if not p.startswith("Done"):
            assert p.startswith("```python\n")


# Posting

def make_hooks(existing=(), avatar_dir=Path("/nonexistent")):
    client = MagicMock()
    client.user.id = BOT_ID
    channel = MagicMock(spec=discord.TextChannel, id=1)
    channel.webhooks = AsyncMock(return_value=list(existing))
    created = MagicMock(send=AsyncMock())
    channel.create_webhook = AsyncMock(return_value=created)
    return Webhooks(client, avatar_dir), channel, created


def own_hook(name="Claude"):
    hook = MagicMock(send=AsyncMock())
    hook.name, hook.user.id = name, BOT_ID
    hook.edit = AsyncMock(return_value=hook)
    return hook


async def test_reuses_own_webhook_and_disables_mentions():
    hook = own_hook()
    hooks, channel, _ = make_hooks([hook])
    await hooks.send(channel, "claude", "hi @everyone")
    channel.create_webhook.assert_not_called()
    hook.edit.assert_not_called()  # no avatar file
    mentions = hook.send.call_args.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)


async def test_one_webhook_per_persona():
    claude, gpt = own_hook("Claude"), own_hook("GPT")
    hooks, channel, _ = make_hooks([gpt, claude])
    await hooks.send(channel, "claude", "a")
    await hooks.send(channel, "gpt", "b")
    claude.send.assert_awaited_once()
    gpt.send.assert_awaited_once()


async def test_ignores_foreign_webhook_and_caches():
    foreign = MagicMock()
    foreign.name, foreign.user.id = "Claude", 7
    hooks, channel, created = make_hooks([foreign])
    await hooks.send(channel, "claude", "a")
    await hooks.send(channel, "claude", "b")
    channel.create_webhook.assert_awaited_once_with(name="Claude", avatar=None)
    channel.webhooks.assert_awaited_once()
    assert created.send.await_count == 2


async def test_avatar_uploaded_on_create_and_reapplied_on_existing(tmp_path):
    (tmp_path / "claude.png").write_bytes(b"png-bytes")
    hooks, channel, _ = make_hooks(avatar_dir=tmp_path)
    await hooks.send(channel, "claude", "a")
    channel.create_webhook.assert_awaited_once_with(name="Claude", avatar=b"png-bytes")

    hook = own_hook()
    hooks, channel, _ = make_hooks([hook], avatar_dir=tmp_path)
    await hooks.send(channel, "claude", "a")
    await hooks.send(channel, "claude", "b")
    hook.edit.assert_awaited_once_with(avatar=b"png-bytes")


async def test_thread_posts_via_parent_webhook():
    hooks, parent, created = make_hooks()
    thread = MagicMock(spec=discord.Thread, id=9)
    thread.parent = parent
    await hooks.send(thread, "claude", "hi")
    assert created.send.call_args.kwargs["thread"] is thread


async def test_recreates_webhook_after_404():
    stale = own_hook()
    stale.send.side_effect = discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Webhook")
    hooks, channel, created = make_hooks()
    channel.webhooks.side_effect = [[stale], []]  # deleted on Discord's side
    await hooks.send(channel, "claude", "hi")
    created.send.assert_awaited_once()


async def test_second_404_raises():
    hooks, channel, created = make_hooks()
    created.send.side_effect = discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Webhook")
    with pytest.raises(discord.NotFound):
        await hooks.send(channel, "claude", "hi")
