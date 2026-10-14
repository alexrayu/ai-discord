from unittest.mock import AsyncMock, MagicMock

import pytest

import review
from models import ImageData
from prompts import load_prompts
from providers.base import ProviderError, Reply
from review import Reviewer

PROMPTS = load_prompts()
MATERIAL = ["Page 1:", ImageData("image/png", "B64")]


def provider(*texts, error=None):
    return MagicMock(respond=AsyncMock(side_effect=error or [Reply(t, "end") for t in texts]))


def reviewer(claude, gpt):
    webhooks = MagicMock(send=AsyncMock())
    return Reviewer(webhooks, {"claude": claude, "gpt": gpt}, PROMPTS, 1568), webhooks


@pytest.fixture(autouse=True)
def material(monkeypatch):
    monkeypatch.setattr(review, "collect", AsyncMock(return_value=MATERIAL))


def thread():
    return MagicMock(id=1, send=AsyncMock())


def upload(note=""):
    return MagicMock(clean_content=note)


async def test_three_steps_in_order_with_shared_prefix():
    claude, gpt = provider("ANALYSIS", "FINAL"), provider("CRITIQUE")
    r, webhooks = reviewer(claude, gpt)
    assert await r.run(thread(), upload("Kenji should look scared."))

    first, final = (c.args for c in claude.respond.await_args_list)
    critique = gpt.respond.await_args.args
    assert first == (PROMPTS["claude"], MATERIAL, [("user", PROMPTS["analysis"])])
    assert critique[0] == PROMPTS["gpt"]
    assert critique[2] == [("user", f"Claude's analysis:\n\nANALYSIS\n\n{PROMPTS['critique']}")]
    # Same prefix as step 1, so the cached material is reused.
    assert final[2][:2] == [("user", PROMPTS["analysis"]), ("assistant", "ANALYSIS")]
    assert final[2][2][1].startswith("Quill's critique:\n\nCRITIQUE\n\nAuthor's note on intent:\nKenji should look scared.")
    assert [(c.args[1], c.args[2].split("\n")[0]) for c in webhooks.send.await_args_list] == [
        ("claude", "**Analysis**"), ("gpt", "**Critique**"), ("claude", "**Final review**")]


async def test_intent_note_only_in_final_step():
    claude, gpt = provider("A", "F"), provider("C")
    r, _ = reviewer(claude, gpt)
    await r.run(thread(), upload("SECRET INTENT"))
    assert "SECRET INTENT" not in str(claude.respond.await_args_list[0]) + str(gpt.respond.await_args)


async def test_no_note():
    claude, gpt = provider("A", "F"), provider("C")
    r, _ = reviewer(claude, gpt)
    await r.run(thread(), upload())
    assert "No author's note on intent was given." in claude.respond.await_args.args[2][2][1]


async def test_provider_error_stops_with_notice():
    claude, gpt = provider("A"), provider(error=ProviderError("APIConnectionError"))
    r, webhooks = reviewer(claude, gpt)
    t = thread()
    assert not await r.run(t, upload())
    assert t.send.await_args.args[0] == "Quill is unavailable right now. Press the button again to retry."
    assert claude.respond.await_count == 1 and webhooks.send.await_count == 1


async def test_refusal_stops_with_notice():
    claude = MagicMock(respond=AsyncMock(return_value=Reply("", "refusal")))
    r, webhooks = reviewer(claude, provider())
    t = thread()
    assert not await r.run(t, upload())
    assert t.send.await_args.args[0] == "Claude declined to answer."
    webhooks.send.assert_not_called()


async def test_unreadable_material_stops_before_any_request(monkeypatch):
    monkeypatch.setattr(review, "collect", AsyncMock(return_value=["[ch.pdf: too large or unavailable]"]))
    claude = provider()
    r, _ = reviewer(claude, provider())
    t = thread()
    assert not await r.run(t, upload())
    assert t.send.await_args.args[0] == "Nothing in the upload could be read: [ch.pdf: too large or unavailable]"
    claude.respond.assert_not_called()


async def test_unexpected_error_is_reported_not_raised():
    claude = MagicMock(respond=AsyncMock(side_effect=RuntimeError("boom")))
    r, _ = reviewer(claude, provider())
    t = thread()
    assert not await r.run(t, upload())
    assert t.send.await_args.args[0] == "The review failed."


# Reading the thread

def history_thread(*messages, name="Review: p1"):
    t = MagicMock(id=1, send=AsyncMock())
    t.name = name

    async def history(**kwargs):
        for m in messages:
            yield m

    t.history = history
    return t


_ids = iter(range(1000, 10**6))


def msg(content, name="Claude", webhook=True, bot=None):
    m = MagicMock(content=content, clean_content=content, webhook_id=1 if webhook else None, id=next(_ids))
    m.author.name = m.author.display_name = name
    m.author.bot = webhook if bot is None else bot
    return m


REVIEW = [msg("**Analysis**\n\nA"), msg("**Critique**\n\nC1", "Quill"), msg("C2", "Quill"),
          msg("**Final review**\n\npart 1"), msg("part 2")]


async def test_steps_rejoined_and_talk_after_final():
    question, answer = msg("why p. 4?", "Walrus", webhook=False), msg("Because.")
    state = await review.read_thread(history_thread(*REVIEW, question, answer))
    assert state.steps == {"analysis": "A", "critique": "C1\n\nC2", "final": "part 1\n\npart 2"}
    assert [(s, n, t) for s, n, t, _ in state.talk] == [("human", "Walrus", "why p. 4?"), ("claude", "Claude", "Because.")]


async def test_follow_up_answers_not_in_final_review():
    t = history_thread(*REVIEW, msg("q", "Walrus", webhook=False), msg("answer"))
    assert await review.final_review(t) == "part 1\n\npart 2"


async def test_newest_run_wins_and_incomplete_is_none():
    old = [msg("**Analysis**\n\nA1"), msg("**Final review**\n\nold"), msg("q", "Walrus", webhook=False)]
    new = [msg("**Analysis**\n\nA2"), msg("**Final review**\n\nnew")]
    state = await review.read_thread(history_thread(*old, *new))
    assert state.steps["final"] == "new" and state.talk == []
    notice = msg("Quill is unavailable.", "Tiramisu", webhook=False, bot=True)
    assert await review.final_review(history_thread(msg("**Analysis**\n\nA"), notice)) is None


@pytest.mark.parametrize(
    "text, pages",
    [("what about p. 4?", [4]), ("pp. 3-5 and page 12", [3, 4, 5, 12]), ("Pages 2 to 3, p4, p. 2", [2, 3, 4]),
     ("p. 1-50", [1, 2, 3, 4, 5, 6]), ("the panel on top", []), ("app 3, step 2", [])],
)
def test_cited_pages(text, pages):
    assert review.cited_pages(text) == pages


# Follow-up answers

async def test_followup_text_only_by_claude_with_history(monkeypatch):
    pages = AsyncMock()
    monkeypatch.setattr(review, "pages", pages)
    claude, gpt = provider("Short answer."), provider()
    r, webhooks = reviewer(claude, gpt)
    earlier_q, earlier_a = msg("first?", "Walrus", webhook=False), msg("First answer.")
    question = msg("and the ending?", "Anna", webhook=False)
    t = history_thread(*REVIEW, earlier_q, earlier_a, question)
    await r.followup(t, MagicMock(), question)
    system, material, turns = claude.respond.await_args.args
    assert system == PROMPTS["claude"]
    assert material[0].startswith("The review of Review: p1:\n\n## Analysis (Claude)\n\nA")
    assert "## Final review (Claude)\n\npart 1\n\npart 2" in material[0] and len(material) == 1
    assert turns[:2] == [("user", "[Walrus]: first?"), ("assistant", "First answer.")]
    assert turns[2] == ("user", f"[Anna]: and the ending?\n\n{PROMPTS['followup']}")
    pages.assert_not_called()
    webhooks.send.assert_awaited_once_with(t, "claude", "Short answer.")


async def test_followup_quill_on_mention_and_cited_pages(monkeypatch):
    image = ImageData("image/png", "P4")
    pages = AsyncMock(return_value=["Page 4 (p4.png):", image])
    monkeypatch.setattr(review, "pages", pages)
    claude, gpt = provider(), provider("Nah.")
    r, webhooks = reviewer(claude, gpt)
    question = msg("@Quill is p. 4 really weak?", "Anna", webhook=False)
    upload = MagicMock()
    await r.followup(history_thread(*REVIEW, question), upload, question)
    _, material, turns = gpt.respond.await_args.args
    assert material[1:] == ["Page 4 (p4.png):", image]
    assert pages.await_args.args == (upload, 1568, [4])
    # Claude's posts are labeled from Quill's view; none here besides the review itself.
    assert turns == [("user", f"[Anna]: @Quill is p. 4 really weak?\n\n{PROMPTS['followup']}")]
    claude.respond.assert_not_called()


async def test_followup_without_finished_review():
    claude = provider()
    r, _ = reviewer(claude, provider())
    question = msg("hello?", "Anna", webhook=False)
    t = history_thread(msg("**Analysis**\n\nA"), question)
    await r.followup(t, None, question)
    assert t.send.await_args.args[0] == "There is no finished review in this thread to ask about."
    claude.respond.assert_not_called()


async def test_followup_provider_error_notice():
    claude = provider(error=ProviderError("APIConnectionError"))
    r, _ = reviewer(claude, provider())
    question = msg("q", "Anna", webhook=False)
    t = history_thread(*REVIEW, question)
    await r.followup(t, None, question)
    assert t.send.await_args.args[0] == "Claude is unavailable right now. Ask again to retry."
