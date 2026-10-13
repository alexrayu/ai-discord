"""Owner commands. From anyone else, and for unknown `!` words, the message is ordinary chat."""

import discord

from config import Config
from orchestrator import Orchestrator
from prompts import load_prompts
from store import Store

BUDGET_RANGE = range(1, 21)


async def run_command(message: discord.Message, orchestrator: Orchestrator, store: Store, config: Config) -> bool:
    """Run an owner command and answer it; False if the message is not a command. Nothing reaches the transcript."""
    name, _, arg = message.content.strip().partition(" ")
    channel_id = message.channel.id
    match name.lower():
        case "!reset":
            await orchestrator.reset(channel_id)
            reply = "Transcript cleared."
        case "!reload":
            try:
                orchestrator.prompts = load_prompts()
            except OSError as e:
                reply = f"Reload failed, old prompts stay: {type(e).__name__}."
            else:
                reply = "Prompts reloaded."
        case "!budget":
            arg = arg.strip()
            if arg.isdigit() and int(arg) in BUDGET_RANGE:
                await store.set_budget(channel_id, int(arg))
                reply = f"Budget set to {int(arg)} AI messages per human message."
            else:
                reply = f"Usage: !budget N, with N from {BUDGET_RANGE.start} to {BUDGET_RANGE.stop - 1}."
        case "!status":
            messages, chars = await store.size(channel_id)
            stored = await store.budget(channel_id)
            budget = f"{stored}" if stored else f"{orchestrator.default_budget} (default)"
            reply = (
                f"Claude: {config.claude_model}, effort {config.claude_effort}\n"
                f"Quill: {config.gpt_model}" + (f", effort {config.gpt_reasoning_effort}" if config.gpt_reasoning_effort else "") + "\n"
                f"Budget: {budget}\n"
                f"Transcript: {messages} messages, {chars:,} characters"
            )
        case _:
            return False
    await message.channel.send(reply, allowed_mentions=discord.AllowedMentions.none())
    return True
