from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from commands import run_command
from config import load_config

CONFIG = load_config({"DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": "222", "ANTHROPIC_API_KEY": "k",
                      "CLAUDE_MODEL": "claude-m", "OPENAI_API_KEY": "o", "GPT_MODEL": "gpt-m"})


def message(content):
    msg = MagicMock(content=content)
    msg.channel = MagicMock(spec=discord.TextChannel, send=AsyncMock())
    return msg


def answer(msg):
    return msg.channel.send.await_args.args[0]


async def test_reload_replaces_prompts():
    reviewer, msg = MagicMock(prompts={}), message("!RELOAD")
    assert await run_command(msg, reviewer, CONFIG)
    assert "Quill" in reviewer.prompts["gpt"] and "Step 3" in reviewer.prompts["final"] and answer(msg) == "Prompts reloaded."


async def test_reload_failure_keeps_old_prompts(monkeypatch):
    import commands
    monkeypatch.setattr(commands, "load_prompts", MagicMock(side_effect=FileNotFoundError("x")))
    reviewer, msg = MagicMock(prompts={}), message("!reload")
    await run_command(msg, reviewer, CONFIG)
    assert reviewer.prompts == {} and "FileNotFoundError" in answer(msg)


async def test_status():
    msg = message("!status")
    await run_command(msg, MagicMock(), CONFIG)
    assert answer(msg) == "Claude: claude-m, effort low\nQuill: gpt-m\nReviews per member and hour: 5"


@pytest.mark.parametrize("content", ["!help", "!reset", "!budget 3", "hello !reload"])
async def test_unknown_is_not_a_command(content):
    assert not await run_command(message(content), MagicMock(), CONFIG)
