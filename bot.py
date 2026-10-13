"""Entry point: Discord client and event handlers."""

import logging
import sys
from pathlib import Path

import discord

from attachments import ImageLoader, from_discord
from config import Config, ConfigError, load_config
from orchestrator import Orchestrator
from prompts import load_prompts
from providers.claude import ClaudeProvider
from providers.gpt import GPTProvider
from ratelimit import RateLimiter
from store import Store
from transcript import TranscriptMessage
from webhooks import Webhooks

DB_PATH = Path(__file__).parent / "transcripts.sqlite"
# These log message content or request bodies at DEBUG; DEBUG mode must never do that.
QUIET_LOGGERS = ("discord", "anthropic", "openai", "httpx", "httpcore", "aiosqlite")

log = logging.getLogger("ai-discord")


def is_chat_message(message: discord.Message, config: Config) -> bool:
    """Any human, only in allowed channels or their threads; never bots or webhooks."""
    if message.webhook_id is not None or message.author.bot:
        return False
    channel = message.channel
    channel_id = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
    return channel_id in config.allowed_channel_ids


def is_owner(message: discord.Message, config: Config) -> bool:
    owner_id = config.owner_user_id or (message.guild and message.guild.owner_id)
    return message.author.id == owner_id


class Bot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.limiter = RateLimiter(config.user_rounds_per_hour)
        self.store: Store | None = None
        self.images: ImageLoader | None = None
        self.orchestrator: Orchestrator | None = None

    async def setup_hook(self) -> None:
        self.store = await Store.open(DB_PATH)
        self.images = ImageLoader(self.store, self.config.image_max_edge)
        self.orchestrator = Orchestrator(
            store=self.store,
            webhooks=Webhooks(self),
            images=self.images,
            providers={"claude": ClaudeProvider(self.config), "gpt": GPTProvider(self.config)},
            prompts=load_prompts(),
            char_limit=self.config.transcript_char_limit,
            budget=self.config.ai_turn_budget,
        )

    async def close(self) -> None:
        await super().close()
        if self.images:
            await self.images.close()
        if self.store:
            await self.store.close()

    async def on_ready(self) -> None:
        log.info("Connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if not is_chat_message(message, self.config) or not (message.clean_content or message.attachments):
            return
        msg = TranscriptMessage(
            channel_id=message.channel.id,
            speaker="human",
            text=message.clean_content,
            display_name=message.author.display_name,
            author_id=message.author.id,
            discord_message_id=message.id,
            attachments=tuple([await from_discord(a) for a in message.attachments]),
        )
        try:
            if is_owner(message, self.config) or not (wait := self.limiter.acquire(message.author.id)):
                await self.orchestrator.run(message.channel, msg)
                return
            # Over the cap: keep the message as context, no reply.
            await self.orchestrator.record(msg)
            if self.limiter.should_notify(message.author.id):
                await message.channel.send(
                    f"{message.author.display_name}, you've hit the hourly limit; try again in {int(wait // 60) + 1} min.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException as e:
            log.error("Discord request failed in channel %s: %s %s", message.channel.id, e.status, e.code)


def main() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        sys.exit(f"Config error: {e}")

    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)

    Bot(config).run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
