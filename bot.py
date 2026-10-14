"""Entry point: Discord client, upload detection and the review button."""

import asyncio
import contextlib
import io
import logging
import re
import sys
from collections import defaultdict
from pathlib import PurePath

import discord
from discord import app_commands

from attachments import check, natural_key
from commands import run_command
from config import Config, ConfigError, load_config
import pdf
from prompts import load_prompts
from providers.claude import ClaudeProvider
from providers.gpt import GPTProvider
from ratelimit import RateLimiter
from review import Reviewer, final_review
from webhooks import Webhooks

# These log message content or request bodies at DEBUG; DEBUG mode must never do that.
QUIET_LOGGERS = ("discord", "anthropic", "openai", "httpx", "httpcore")
REQUEST, RUNNING, DOWNLOAD = "Request review", "Review running", "Download PDF"
MENU = "Request review"  # message context menu entry
THREAD_NAME_MAX = 100

log = logging.getLogger("ai-discord")


def is_upload(message: discord.Message, config: Config) -> bool:
    """A human's message in an allowed channel; threads (the reviews themselves), bots and webhooks are ignored."""
    if message.webhook_id is not None or message.author.bot:
        return False
    return message.channel.id in config.allowed_channel_ids


def is_question(message: discord.Message, config: Config, bot_id: int) -> bool:
    """A human's message in a review thread: a thread the bot started in an allowed channel."""
    if message.webhook_id is not None or message.author.bot:
        return False
    channel = message.channel
    return isinstance(channel, discord.Thread) and channel.parent_id in config.allowed_channel_ids and channel.owner_id == bot_id


def is_owner(user_id: int, guild: discord.Guild | None, config: Config) -> bool:
    return user_id == (config.owner_user_id or (guild and guild.owner_id))


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"review:(?P<id>\d+)"):
    """Survives restarts: Discord sends the custom id, which carries the upload message id."""

    def __init__(self, upload_id: int, label: str = REQUEST, disabled: bool = False):
        super().__init__(discord.ui.Button(
            label=label, style=discord.ButtonStyle.primary, custom_id=f"review:{upload_id}", disabled=disabled
        ))
        self.upload_id = upload_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.start_review(interaction, self.upload_id)


class PdfButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pdf:(?P<id>\d+)"):
    """On the finished review's link message; the id is the upload's, which is also its thread's id."""

    def __init__(self, upload_id: int):
        super().__init__(discord.ui.Button(label=DOWNLOAD, style=discord.ButtonStyle.secondary, custom_id=f"pdf:{upload_id}"))
        self.upload_id = upload_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.send_pdf(interaction, self.upload_id)


def button(upload_id: int, label: str = REQUEST, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(upload_id, label, disabled))
    return view


def pdf_button(upload_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PdfButton(upload_id))
    return view


def thread_name(upload: discord.Message) -> str:
    first = min((a.filename for a in upload.attachments), key=natural_key)
    return f"Review: {PurePath(first).stem}"[:THREAD_NAME_MAX]


class Bot(discord.Client):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.limiter = RateLimiter(config.user_reviews_per_hour)
        self.followup_limiter = RateLimiter(config.followups_per_hour)
        self.thread_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)  # one answer at a time per thread
        self.reviewer: Reviewer | None = None
        self.reviews: dict[int, asyncio.Task] = {}  # by upload message id; also keeps the tasks referenced
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        self.reviewer = Reviewer(
            webhooks=Webhooks(self),
            providers={"claude": ClaudeProvider(self.config), "gpt": GPTProvider(self.config)},
            prompts=load_prompts(),
            image_max_edge=self.config.image_max_edge,
        )
        self.add_dynamic_items(ReviewButton, PdfButton)
        self.tree.add_command(app_commands.ContextMenu(name=MENU, callback=self.review_from_menu))
        # Overwrites the registered commands with this set; unchanged sets don't count against Discord's limits.
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if is_question(message, self.config, self.user.id):
            await self._answer(message)
            return
        if not is_upload(message, self.config):
            return
        try:
            if is_owner(message.author.id, message.guild, self.config) and await run_command(message, self.reviewer, self.config):
                return
            usable, oversized = check(message)
            if usable:
                await message.reply(view=button(message.id), mention_author=False)
            elif oversized:
                await message.reply("Too large to review: " + "; ".join(oversized) + ".", mention_author=False)
        except discord.HTTPException as e:
            log.error("Discord request failed in channel %s: %s %s", message.channel.id, e.status, e.code)

    async def _answer(self, question: discord.Message) -> None:
        """Follow-up in a review thread, answered in order per thread."""
        thread = question.channel
        try:
            if thread.id in self.reviews:
                await question.reply("The review is still running; ask once it's done.", mention_author=False)
                return
            user_id = question.author.id
            if not is_owner(user_id, question.guild, self.config) and (wait := self.followup_limiter.acquire(user_id)):
                await question.reply(f"You've hit the hourly question limit; try again in {int(wait // 60) + 1} min.",
                                     mention_author=False)
                return
            async with self.thread_locks[thread.id]:
                try:
                    upload = await thread.parent.fetch_message(thread.id)  # the thread's starter message
                except discord.NotFound:
                    upload = None  # deleted: answers can't attach pages, the review text still works
                await self.reviewer.followup(thread, upload, question)
        except discord.HTTPException as e:
            log.error("Discord request failed in thread %s: %s %s", thread.id, e.status, e.code)

    async def start_review(self, interaction: discord.Interaction, upload_id: int) -> None:
        """Button click: the button message turns into the review's status message."""
        if interaction.channel_id not in self.config.allowed_channel_ids or not await self._may_start(interaction, upload_id):
            return
        try:
            upload = await interaction.channel.fetch_message(upload_id)
        except discord.NotFound:
            await interaction.response.edit_message(content="The upload was deleted.", view=None)
            return
        await interaction.response.edit_message(view=button(upload_id, RUNNING, disabled=True))
        self._launch(upload, interaction.message)

    async def review_from_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Message menu > Apps > Request review: any earlier upload, also one without a button or already reviewed."""
        if not is_upload(message, self.config):
            await interaction.response.send_message("Only members' uploads in review channels can be reviewed.", ephemeral=True)
            return
        usable, oversized = check(message)
        if not usable:
            reason = "Too large to review: " + "; ".join(oversized) + "." if oversized else "No images, PDFs or scripts to review."
            await interaction.response.send_message(reason, ephemeral=True)
            return
        if not await self._may_start(interaction, message.id):
            return
        await interaction.response.send_message("Review started.", ephemeral=True)
        status = await message.reply(view=button(message.id, RUNNING, disabled=True), mention_author=False)
        self._launch(message, status)

    async def _may_start(self, interaction: discord.Interaction, upload_id: int) -> bool:
        """Not running yet, and within the hourly cap (owner exempt); otherwise the user is told why."""
        if upload_id in self.reviews:
            await interaction.response.send_message("This review is already running.", ephemeral=True)
            return False
        user = interaction.user
        if not is_owner(user.id, interaction.guild, self.config) and (wait := self.limiter.acquire(user.id)):
            await interaction.response.send_message(
                f"You've hit the hourly review limit; try again in {int(wait // 60) + 1} min.", ephemeral=True
            )
            return False
        return True

    def _launch(self, upload: discord.Message, status: discord.Message) -> None:
        task = asyncio.create_task(self._review(upload, status))
        self.reviews[upload.id] = task
        task.add_done_callback(lambda _: self.reviews.pop(upload.id, None))

    async def _review(self, upload: discord.Message, status: discord.Message) -> None:
        """Another run on the same upload (retry or re-request) posts into its existing thread."""
        try:
            thread = await self._thread(upload)
            if await self.reviewer.run(thread, upload):
                await status.edit(content=f"Review: {thread.mention}", view=pdf_button(upload.id))
                return
        except discord.HTTPException as e:
            log.error("Discord request failed for upload %s: %s %s", upload.id, e.status, e.code)
        with contextlib.suppress(discord.HTTPException):
            await status.edit(view=button(upload.id))

    async def _thread(self, upload: discord.Message) -> discord.Thread:
        """The upload's thread (its id is the upload's), reopened if archived; created if there is none."""
        try:
            thread = upload.thread or await self.fetch_channel(upload.id)
        except discord.NotFound:
            return await upload.create_thread(name=thread_name(upload))
        if thread.archived:
            thread = await thread.edit(archived=False)
        return thread

    async def send_pdf(self, interaction: discord.Interaction, upload_id: int) -> None:
        """The final review as a PDF, visible only to the requester. Rebuilt from the thread, nothing stored."""
        if interaction.channel_id not in self.config.allowed_channel_ids:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = await self.fetch_channel(upload_id)
            text = await final_review(thread)
        except discord.HTTPException:
            text = None
        if text is None:
            await interaction.followup.send("No finished review found; the thread may have been deleted.", ephemeral=True)
            return
        data = await asyncio.to_thread(pdf.render, thread.name, text)
        name = re.sub(r"[^\w.-]+", "-", thread.name).strip("-").lower() or "review"
        await interaction.followup.send(file=discord.File(io.BytesIO(data), filename=f"{name}.pdf"), ephemeral=True)


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
