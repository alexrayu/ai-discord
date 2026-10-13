from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock

import pytest

import orchestrator
from orchestrator import ADDRESSED, MUST_REPLY, YOUR_TURN, Orchestrator
from providers.base import ProviderError, Reply
from store import Store
from transcript import TranscriptMessage


class Typing(AbstractAsyncContextManager):
    async def __aexit__(self, *exc):
        return None


NO_IMAGES = MagicMock(load=AsyncMock(return_value={}))


class FakeProvider:
    """Returns the given results in order, then PASS."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def respond(self, system, transcript, images, hint):
        self.calls.append((system, [m.text for m in transcript], hint))
        result = self.results.pop(0) if self.results else Reply("PASS", "end")
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def fixed_order(monkeypatch):
    """Shuffle leaves the order as is: Claude, then Quill."""
    monkeypatch.setattr(orchestrator.random, "shuffle", lambda queue: None)


@pytest.fixture
async def setup(tmp_path):
    store = await Store.open(tmp_path / "t.sqlite")
    webhooks = MagicMock(send=AsyncMock())
    channel = MagicMock(id=1, send=AsyncMock())
    channel.typing = lambda: Typing()

    def make(*results):
        provider = FakeProvider(*results)
        return Orchestrator(store, webhooks, NO_IMAGES, {"claude": provider}, {"claude": "SYS"}, 10_000, 3), provider

    def make_two(claude, quill, budget=3):
        return Orchestrator(store, webhooks, NO_IMAGES, {"claude": claude, "gpt": quill}, {"claude": "C", "gpt": "Q"}, 10_000, budget)

    yield make, make_two, store, webhooks, channel
    await store.close()


def human(text):
    return TranscriptMessage(channel_id=1, speaker="human", text=text, display_name="Walrus", author_id=5)


def posted(webhooks):
    return [(c.args[1], c.args[2]) for c in webhooks.send.await_args_list]


async def test_reply_posted_and_recorded(setup):
    make, _, store, webhooks, channel = setup
    orch, provider = make(Reply("Nope.", "end"))
    await orch.run(channel, human("hi"))
    assert provider.calls[0] == ("SYS", ["hi"], YOUR_TURN)
    webhooks.send.assert_awaited_once_with(channel, "claude", "Nope.")
    assert [(m.speaker, m.text) for m in await store.recent(1, 1000)] == [("human", "hi"), ("claude", "Nope.")]


async def test_pass_posts_nothing(setup):
    make, make_two, store, webhooks, channel = setup
    orch, _ = make(Reply("PASS", "end"))
    await orch.run(channel, human("hi"))
    webhooks.send.assert_not_called()
    channel.send.assert_not_called()
    assert len(await store.recent(1, 1000)) == 1


async def test_refusal_posts_notice(setup):
    make, make_two, store, webhooks, channel = setup
    orch, _ = make(Reply("", "refusal"))
    await orch.run(channel, human("hi"))
    webhooks.send.assert_not_called()
    channel.send.assert_awaited_once_with("Claude declined to answer.")


async def test_provider_error_posts_notice_without_crashing(setup):
    make, make_two, store, webhooks, channel = setup
    orch, _ = make(ProviderError("RateLimitError 429"))
    await orch.run(channel, human("hi"))
    channel.send.assert_awaited_once_with("Claude is unavailable right now.")
    assert len(await store.recent(1, 1000)) == 1


async def test_record_appends_without_reply(setup):
    make, make_two, store, webhooks, channel = setup
    orch, provider = make()
    await orch.record(human("over cap"))
    assert provider.calls == []
    assert [m.text for m in await store.recent(1, 1000)] == ["over cap"]


async def test_one_ai_failing_does_not_stop_the_other(setup):
    make, make_two, store, webhooks, channel = setup
    orch = make_two(FakeProvider(ProviderError("APIConnectionError")), FakeProvider(Reply("Still here.", "end")))
    await orch.run(channel, human("hi"))
    channel.send.assert_awaited_once_with("Claude is unavailable right now.")
    assert posted(webhooks) == [("gpt", "Still here.")]


# Turn loop

async def test_second_ai_sees_first_reply(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(Reply("Use G-pen.", "end")), FakeProvider(Reply("Maru-pen is finer.", "end"))
    await make_two(claude, quill).run(channel, human("best nib?"))
    assert claude.calls[0] == ("C", ["best nib?"], YOUR_TURN)
    assert quill.calls[0] == ("Q", ["best nib?", "Use G-pen."], YOUR_TURN)
    assert posted(webhooks) == [("claude", "Use G-pen."), ("gpt", "Maru-pen is finer.")]


async def test_stops_when_both_pass(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(Reply("A.", "end")), FakeProvider()
    await make_two(claude, quill).run(channel, human("hi"))
    # Round 1: Claude speaks, Quill passes. Round 2: both pass, loop ends.
    assert (len(claude.calls), len(quill.calls)) == (2, 2)
    assert posted(webhooks) == [("claude", "A.")]


async def test_stops_at_budget(setup):
    make, make_two, store, webhooks, channel = setup
    replies = [Reply(f"r{i}", "end") for i in range(5)]
    claude, quill = FakeProvider(*replies), FakeProvider(*replies)
    await make_two(claude, quill, budget=3).run(channel, human("hi"))
    assert [speaker for speaker, _ in posted(webhooks)] == ["claude", "gpt", "claude"]
    assert len(quill.calls) == 1


async def test_hint_never_stored(setup):
    make, make_two, store, webhooks, channel = setup
    orch, _ = make(Reply("Ok.", "end"))
    await orch.run(channel, human("hi"))
    assert all("[System]" not in m.text for m in await store.recent(1, 1000))


# Mentions

async def test_mentioned_ai_goes_first_and_other_gets_addressed_hint(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(), FakeProvider(Reply("Maru.", "end"))
    await make_two(claude, quill).run(channel, human("@GPT best nib?"))
    assert quill.calls[0][2] == ADDRESSED
    assert claude.calls[0][1:] == (["@GPT best nib?", "Maru."], "This was addressed to @Quill. PASS unless you have a correction or a real objection.")
    assert posted(webhooks)[0] == ("gpt", "Maru.")


async def test_both_mentioned_both_must_reply(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(Reply("G.", "end")), FakeProvider(Reply("Maru.", "end"))
    await make_two(claude, quill).run(channel, human("@Claude @Quill best nib?"))
    assert (claude.calls[0][2], quill.calls[0][2]) == (ADDRESSED, ADDRESSED)


async def test_must_reply_retried_once_then_pass_accepted(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(), FakeProvider(Reply("PASS", "end"), Reply("PASS", "end"))
    await make_two(claude, quill).run(channel, human("@Quill hi"))
    assert [c[2] for c in quill.calls] == [ADDRESSED, MUST_REPLY]
    webhooks.send.assert_not_called()


async def test_must_reply_retry_can_succeed(setup):
    make, make_two, store, webhooks, channel = setup
    claude, quill = FakeProvider(), FakeProvider(Reply("PASS", "end"), Reply("Fine.", "end"))
    await make_two(claude, quill).run(channel, human("@Quill hi"))
    assert posted(webhooks) == [("gpt", "Fine.")]


async def test_ai_addressed_after_speaking_goes_first_next_round(setup, monkeypatch):
    make, make_two, store, webhooks, channel = setup
    orders = iter([False, True, True])  # reverse the order from round 2 on
    monkeypatch.setattr(orchestrator.random, "shuffle", lambda q: q.reverse() if next(orders) else None)
    claude, quill = FakeProvider(Reply("G-pen.", "end"), Reply("Pressure control.", "end")), FakeProvider(Reply("@Claude why?", "end"))
    await make_two(claude, quill).run(channel, human("best nib?"))
    assert posted(webhooks) == [("claude", "G-pen."), ("gpt", "@Claude why?"), ("claude", "Pressure control.")]
    assert claude.calls[1][2] == ADDRESSED


async def test_out_of_credits_notice_once_per_run(setup):
    make, make_two, store, webhooks, channel = setup
    claude = FakeProvider(*[ProviderError("BadRequestError 400", out_of_credits=True)] * 3)
    quill = FakeProvider(Reply("a", "end"), Reply("b", "end"))
    await make_two(claude, quill).run(channel, human("hi"))
    assert len(claude.calls) == 1
    (notice,), _ = channel.send.await_args
    assert notice in [m.format(name="Claude") for m in orchestrator.OUT_OF_CREDITS]
    channel.send.assert_awaited_once()
    assert posted(webhooks) == [("gpt", "a"), ("gpt", "b")]
