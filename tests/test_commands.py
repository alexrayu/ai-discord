from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot import Bot
from commands import run_command
from config import load_config
from store import Store
from transcript import TranscriptMessage

OWNER, CHANNEL = 111, 222
CONFIG = load_config({"DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": str(CHANNEL), "ANTHROPIC_API_KEY": "k",
                      "CLAUDE_MODEL": "claude-m", "OPENAI_API_KEY": "o", "GPT_MODEL": "gpt-m", "OWNER_USER_ID": str(OWNER)})


@pytest.fixture
async def store(tmp_path):
    store = await Store.open(tmp_path / "t.sqlite")
    yield store
    await store.close()


def message(content, author_id=OWNER):
    msg = MagicMock(content=content, clean_content=content, attachments=[], webhook_id=None)
    msg.author.id, msg.author.bot, msg.author.display_name = author_id, False, "Walrus"
    msg.channel = MagicMock(spec=discord.TextChannel, id=CHANNEL, send=AsyncMock())
    return msg


def orchestrator():
    return MagicMock(reset=AsyncMock(), default_budget=3, prompts={})


def answer(msg):
    return msg.channel.send.await_args.args[0]


async def test_reset(store):
    orch, msg = orchestrator(), message("!reset")
    assert await run_command(msg, orch, store, CONFIG)
    orch.reset.assert_awaited_once_with(CHANNEL)
    assert answer(msg) == "Transcript cleared."


async def test_reload_replaces_prompts(store):
    orch, msg = orchestrator(), message("!RELOAD")
    assert await run_command(msg, orch, store, CONFIG)
    assert "Quill" in orch.prompts["gpt"] and answer(msg) == "Prompts reloaded."


async def test_reload_failure_keeps_old_prompts(store, monkeypatch):
    import commands
    monkeypatch.setattr(commands, "load_prompts", MagicMock(side_effect=FileNotFoundError("x")))
    orch, msg = orchestrator(), message("!reload")
    await run_command(msg, orch, store, CONFIG)
    assert orch.prompts == {} and "FileNotFoundError" in answer(msg)


@pytest.mark.parametrize("arg, stored", [("5", 5), ("1", 1), ("20", 20), ("0", None), ("21", None), ("x", None), ("", None), ("-3", None)])
async def test_budget(store, arg, stored):
    msg = message(f"!budget {arg}")
    assert await run_command(msg, orchestrator(), store, CONFIG)
    assert await store.budget(CHANNEL) == stored
    assert answer(msg).startswith("Budget set" if stored else "Usage")


async def test_status(store):
    await store.append(TranscriptMessage(channel_id=CHANNEL, speaker="human", text="hello", display_name="W"))
    msg = message("!status")
    await run_command(msg, orchestrator(), store, CONFIG)
    assert answer(msg) == "Claude: claude-m, effort low\nQuill: gpt-m\nBudget: 3 (default)\nTranscript: 1 messages, 5 characters"
    await store.set_budget(CHANNEL, 7)
    await run_command(msg, orchestrator(), store, CONFIG)
    assert "Budget: 7\n" in answer(msg)


@pytest.mark.parametrize("content", ["!help", "!resetting", "hello !reset"])
async def test_unknown_is_not_a_command(store, content):
    assert not await run_command(message(content), orchestrator(), store, CONFIG)


# Dispatch in the bot

@pytest.fixture
def bot(store):
    bot = Bot(CONFIG)
    bot.store, bot.orchestrator = store, MagicMock(run=AsyncMock(), record=AsyncMock(), reset=AsyncMock(), default_budget=3)
    return bot


async def test_owner_command_not_chat(bot):
    await bot.on_message(message("!reset"))
    bot.orchestrator.reset.assert_awaited_once()
    bot.orchestrator.run.assert_not_called()


async def test_member_command_is_chat(bot):
    await bot.on_message(message("!reset", author_id=333))
    bot.orchestrator.reset.assert_not_called()
    assert bot.orchestrator.run.await_args.args[1].text == "!reset"


async def test_owner_unknown_bang_is_chat(bot):
    await bot.on_message(message("!help"))
    bot.orchestrator.run.assert_awaited_once()
