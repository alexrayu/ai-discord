import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot as bot_module
from bot import RUNNING, Bot, PdfButton, ReviewButton, is_owner, is_question, is_upload, thread_name
from config import load_config

OWNER, MEMBER, CHANNEL, BOT_ID = 111, 333, 222, 999
ENV = {"DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": str(CHANNEL), "ANTHROPIC_API_KEY": "k", "CLAUDE_MODEL": "m",
       "OPENAI_API_KEY": "o", "GPT_MODEL": "g"}
CONFIG = load_config({**ENV, "OWNER_USER_ID": str(OWNER), "USER_REVIEWS_PER_HOUR": "1"})


def message(content="", author_id=MEMBER, bot=False, webhook_id=None, channel_id=CHANNEL, files=(), size=10):
    msg = MagicMock(id=5, content=content, webhook_id=webhook_id, reply=AsyncMock())
    msg.author.id, msg.author.bot = author_id, bot
    msg.channel = MagicMock(spec=discord.TextChannel, id=channel_id, send=AsyncMock())
    msg.attachments = [SimpleNamespace(filename=f, content_type="", size=size) for f in files]
    return msg


# Filtering and owner

def test_member_upload_in_allowed_channel():
    assert is_upload(message(), CONFIG)


@pytest.mark.parametrize(
    "msg",
    [message(webhook_id=1), message(bot=True), message(channel_id=444), message(channel_id=999)],
    ids=["webhook", "bot", "other-channel", "thread"],
)
def test_ignored(msg):
    assert not is_upload(msg, CONFIG)


def test_owner():
    assert is_owner(OWNER, None, CONFIG) and not is_owner(MEMBER, None, CONFIG)
    config = load_config(ENV)
    assert is_owner(MEMBER, SimpleNamespace(owner_id=MEMBER), config)
    assert not is_owner(MEMBER, None, config)


def test_thread_name_from_first_file():
    upload = SimpleNamespace(attachments=[SimpleNamespace(filename="p10.png"), SimpleNamespace(filename="p2.png")])
    assert thread_name(upload) == "Review: p2"


# Button under uploads

@pytest.fixture
def client():
    client = Bot(CONFIG)
    client._connection.user = MagicMock(id=BOT_ID)
    client.reviewer = MagicMock(run=AsyncMock(return_value=True), followup=AsyncMock())
    client.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "no thread"))
    return client


async def test_reviewable_upload_gets_button(client):
    msg = message(files=["p1.png"])
    await client.on_message(msg)
    view = msg.reply.await_args.kwargs["view"]
    assert view.children[0].custom_id == "review:5"


async def test_oversized_upload_gets_reason_not_button(client):
    msg = message(files=["ch.pdf"], size=200 * 1024 * 1024)
    await client.on_message(msg)
    assert msg.reply.await_args.args[0] == "Too large to review: ch.pdf is 200.0 MB, max 100 MB."


async def test_plain_text_gets_no_button(client):
    msg = message("hello")
    await client.on_message(msg)
    msg.reply.assert_not_called()


async def test_owner_command_runs(client, monkeypatch):
    run_command = AsyncMock(return_value=True)
    monkeypatch.setattr(bot_module, "run_command", run_command)
    msg = message("!status", author_id=OWNER, files=["p1.png"])
    await client.on_message(msg)
    run_command.assert_awaited_once()
    msg.reply.assert_not_called()


async def test_member_cannot_run_commands(client, monkeypatch):
    run_command = AsyncMock(return_value=True)
    monkeypatch.setattr(bot_module, "run_command", run_command)
    await client.on_message(message("!status"))
    run_command.assert_not_called()


async def test_custom_id_round_trip():
    item = await ReviewButton.from_custom_id(MagicMock(), MagicMock(), ReviewButton.__discord_ui_compiled_template__.match("review:42"))
    assert item.upload_id == 42


# Click

def interaction(user_id=MEMBER, upload=None):
    upload = upload or SimpleNamespace(id=7, thread=None, create_thread=AsyncMock(return_value=MagicMock(mention="#t")),
                                       attachments=[SimpleNamespace(filename="p1.png")])
    inter = MagicMock(channel_id=CHANNEL, guild=None)
    inter.user.id = user_id
    inter.channel.fetch_message = AsyncMock(return_value=upload)
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.message.edit = AsyncMock()
    return inter, upload


async def test_click_runs_review_and_links_thread(client):
    inter, upload = interaction()
    await client.start_review(inter, 7)
    view = inter.response.edit_message.await_args.kwargs["view"]
    assert view.children[0].item.label == RUNNING and view.children[0].item.disabled
    await client.reviews[7]
    upload.create_thread.assert_awaited_once_with(name="Review: p1")
    edit = inter.message.edit.await_args.kwargs
    assert edit["content"] == "Review: #t" and edit["view"].children[0].custom_id == "pdf:7"
    await asyncio.sleep(0)
    assert 7 not in client.reviews


async def test_failed_review_reenables_button_and_retry_reuses_thread(client):
    client.reviewer.run = AsyncMock(return_value=False)
    inter, upload = interaction(user_id=OWNER)
    await client.start_review(inter, 7)
    await client.reviews[7]
    assert not inter.message.edit.await_args.kwargs["view"].children[0].item.disabled
    upload.thread = MagicMock(archived=False)
    await asyncio.sleep(0)
    await client.start_review(inter, 7)
    await client.reviews[7]
    assert client.reviewer.run.await_args.args[0] is upload.thread


async def test_running_review_not_started_twice(client):
    client.reviews[7] = MagicMock()
    inter, _ = interaction()
    await client.start_review(inter, 7)
    assert inter.response.send_message.await_args.kwargs["ephemeral"]
    inter.channel.fetch_message.assert_not_called()


async def test_rate_limit_for_members_not_owner(client):
    client.limiter.acquire(MEMBER)
    inter, _ = interaction()
    await client.start_review(inter, 7)
    assert "hourly review limit" in inter.response.send_message.await_args.args[0]
    owner, _ = interaction(user_id=OWNER)
    client.limiter.acquire(OWNER)
    await client.start_review(owner, 7)
    owner.response.edit_message.assert_awaited_once()
    await client.reviews[7]


async def test_deleted_upload(client):
    inter, _ = interaction()
    inter.channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    await client.start_review(inter, 7)
    inter.response.edit_message.assert_awaited_once_with(content="The upload was deleted.", view=None)
    assert 7 not in client.reviews


# PDF download

def pdf_interaction(channel_id=CHANNEL):
    inter = MagicMock(channel_id=channel_id)
    inter.response.defer = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


async def test_pdf_sent_privately(client, monkeypatch):
    monkeypatch.setattr(bot_module, "final_review", AsyncMock(return_value="## Verdict"))
    monkeypatch.setattr(bot_module.pdf, "render", MagicMock(return_value=b"%PDF-1.7"))
    client.fetch_channel = AsyncMock(return_value=MagicMock(name="thread"))
    client.fetch_channel.return_value.name = "Review: Ch 1/p1"
    inter = pdf_interaction()
    await client.send_pdf(inter, 7)
    client.fetch_channel.assert_awaited_once_with(7)
    bot_module.pdf.render.assert_called_once_with("Review: Ch 1/p1", "## Verdict")
    kwargs = inter.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] and kwargs["file"].filename == "review-ch-1-p1.pdf"


async def test_pdf_without_review(client):
    client.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    inter = pdf_interaction()
    await client.send_pdf(inter, 7)
    assert "No finished review" in inter.followup.send.await_args.args[0]


async def test_pdf_custom_id_round_trip():
    item = await PdfButton.from_custom_id(MagicMock(), MagicMock(), PdfButton.__discord_ui_compiled_template__.match("pdf:42"))
    assert item.upload_id == 42


# Message menu: Request review

def menu_interaction(user_id=MEMBER):
    inter = MagicMock(guild=None)
    inter.user.id = user_id
    inter.response.send_message = AsyncMock()
    return inter


async def test_menu_review_posts_status_and_runs(client):
    upload = message(files=["p1.png"])
    upload.thread, upload.create_thread = None, AsyncMock(return_value=MagicMock(mention="#t"))
    status = MagicMock(edit=AsyncMock())
    upload.reply = AsyncMock(return_value=status)
    inter = menu_interaction()
    await client.review_from_menu(inter, upload)
    assert inter.response.send_message.await_args.args[0] == "Review started."
    assert upload.reply.await_args.kwargs["view"].children[0].item.label == RUNNING
    await client.reviews[5]
    assert status.edit.await_args.kwargs["content"] == "Review: #t"


@pytest.mark.parametrize(
    "msg, reason",
    [
        (message(files=["p1.png"], bot=True), "Only members' uploads"),
        (message(files=["p1.png"], channel_id=444), "Only members' uploads"),
        (message("just text"), "No images, PDFs or scripts"),
        (message(files=["ch.pdf"], size=200 * 1024 * 1024), "Too large to review: ch.pdf"),
    ],
    ids=["bot", "other-channel", "no-files", "too-large"],
)
async def test_menu_refusals(client, msg, reason):
    inter = menu_interaction()
    await client.review_from_menu(inter, msg)
    assert inter.response.send_message.await_args.args[0].startswith(reason)
    msg.reply.assert_not_called()


async def test_menu_respects_rate_limit(client):
    client.limiter.acquire(MEMBER)
    inter, msg = menu_interaction(), message(files=["p1.png"])
    await client.review_from_menu(inter, msg)
    assert "hourly review limit" in inter.response.send_message.await_args.args[0]
    assert not client.reviews


async def test_existing_archived_thread_reopened(client):
    reopened = MagicMock()
    archived = MagicMock(archived=True, edit=AsyncMock(return_value=reopened))
    client.fetch_channel = AsyncMock(return_value=archived)
    upload = SimpleNamespace(id=7, thread=None, create_thread=AsyncMock())
    assert await client._thread(upload) is reopened
    archived.edit.assert_awaited_once_with(archived=False)
    upload.create_thread.assert_not_called()


# Follow-up questions in review threads

def question(author_id=MEMBER, owner_id=BOT_ID, parent_id=CHANNEL, bot=False):
    msg = MagicMock(webhook_id=None, reply=AsyncMock(), guild=None)
    msg.author.id, msg.author.bot = author_id, bot
    msg.channel = MagicMock(spec=discord.Thread, id=7, parent_id=parent_id, owner_id=owner_id)
    msg.channel.parent.fetch_message = AsyncMock(return_value="UPLOAD")
    return msg


def test_is_question():
    assert is_question(question(), CONFIG, BOT_ID)
    assert not is_question(question(owner_id=5), CONFIG, BOT_ID)  # someone else's thread
    assert not is_question(question(parent_id=444), CONFIG, BOT_ID)
    assert not is_question(question(bot=True), CONFIG, BOT_ID)
    assert not is_question(message(), CONFIG, BOT_ID)  # channel message, not a thread


async def test_question_answered_with_upload(client):
    q = question()
    await client.on_message(q)
    client.reviewer.followup.assert_awaited_once_with(q.channel, "UPLOAD", q)


async def test_question_with_deleted_upload_still_answered(client):
    q = question()
    q.channel.parent.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    await client.on_message(q)
    client.reviewer.followup.assert_awaited_once_with(q.channel, None, q)


async def test_question_during_review(client):
    client.reviews[7] = MagicMock()
    q = question()
    await client.on_message(q)
    assert "still running" in q.reply.await_args.args[0]
    client.reviewer.followup.assert_not_called()


async def test_question_cap_for_members_not_owner(client):
    for _ in range(30):
        client.followup_limiter.acquire(MEMBER)
    q = question()
    await client.on_message(q)
    assert "hourly question limit" in q.reply.await_args.args[0]
    owner_q = question(author_id=OWNER)
    await client.on_message(owner_q)
    client.reviewer.followup.assert_awaited_once()
