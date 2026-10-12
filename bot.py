"""Entry point: Discord client and event handlers."""

import logging
import sys

import discord

from config import Config, ConfigError, load_config
from webhooks import Webhooks

log = logging.getLogger("ai-discord")


def is_chat_message(message: discord.Message, config: Config) -> bool:
    """Any human, only in allowed channels or their threads; never bots or webhooks."""
    if message.webhook_id is not None or message.author.bot:
        return False
    channel = message.channel
    channel_id = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
    return channel_id in config.allowed_channel_ids


def main() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        sys.exit(f"Config error: {e}")

    # discord.py stays at INFO so DEBUG never dumps gateway payloads with message content.
    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    logging.getLogger("discord").setLevel(logging.INFO)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    webhooks = Webhooks(client)

    @client.event
    async def on_ready() -> None:
        log.info("Connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if not is_chat_message(message, config) or not message.content:
            return
        try:
            await webhooks.send(message.channel, "Claude", message.content, config.claude_avatar_url)
        except discord.HTTPException as e:
            log.error("Echo failed in channel %s: %s %s", message.channel.id, e.status, e.code)

    client.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
