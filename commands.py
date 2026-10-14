"""Owner commands. From anyone else, and for unknown `!` words, the message is an ordinary upload or ignored."""

import discord

from config import Config
from prompts import load_prompts
from review import Reviewer


async def run_command(message: discord.Message, reviewer: Reviewer, config: Config) -> bool:
    """Run an owner command and answer it; False if the message is not a command."""
    match message.content.strip().lower():
        case "!reload":
            try:
                reviewer.prompts = load_prompts()
            except OSError as e:
                reply = f"Reload failed, old prompts stay: {type(e).__name__}."
            else:
                reply = "Prompts reloaded."
        case "!status":
            reply = (
                f"Claude: {config.claude_model}, effort {config.claude_effort}\n"
                f"Quill: {config.gpt_model}" + (f", effort {config.gpt_reasoning_effort}" if config.gpt_reasoning_effort else "") + "\n"
                f"Reviews per member and hour: {config.user_reviews_per_hour}"
            )
        case _:
            return False
    await message.channel.send(reply, allowed_mentions=discord.AllowedMentions.none())
    return True
