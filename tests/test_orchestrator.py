from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import Orchestrator
from providers.base import ProviderError, Reply
from store import Store
from transcript import TranscriptMessage


class Typing(AbstractAsyncContextManager):
    async def __aexit__(self, *exc):
        return None


class FakeProvider:
    name = "claude"

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def respond(self, system, transcript):
        self.calls.append((system, [m.text for m in transcript]))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
async def setup(tmp_path):
    store = await Store.open(tmp_path / "t.sqlite")
    webhooks = MagicMock(send=AsyncMock())
    channel = MagicMock(id=1, send=AsyncMock())
    channel.typing = lambda: Typing()

    def make(*results):
        provider = FakeProvider(*results)
        return Orchestrator(store, webhooks, {"claude": provider}, {"claude": "SYS"}, 10_000), provider

    yield make, store, webhooks, channel
    await store.close()


def human(text):
    return TranscriptMessage(channel_id=1, speaker="human", text=text, display_name="Walrus", author_id=5)


async def test_reply_posted_and_recorded(setup):
    make, store, webhooks, channel = setup
    orch, provider = make(Reply("Nope.", "end"))
    await orch.run(channel, human("hi"))
    assert provider.calls == [("SYS", ["hi"])]
    webhooks.send.assert_awaited_once_with(channel, "claude", "Nope.")
    assert [(m.speaker, m.text) for m in await store.recent(1, 1000)] == [("human", "hi"), ("claude", "Nope.")]


async def test_pass_posts_nothing(setup):
    make, store, webhooks, channel = setup
    orch, _ = make(Reply("PASS", "end"))
    await orch.run(channel, human("hi"))
    webhooks.send.assert_not_called()
    channel.send.assert_not_called()
    assert len(await store.recent(1, 1000)) == 1


async def test_refusal_posts_notice(setup):
    make, store, webhooks, channel = setup
    orch, _ = make(Reply("", "refusal"))
    await orch.run(channel, human("hi"))
    webhooks.send.assert_not_called()
    channel.send.assert_awaited_once_with("Claude declined to answer.")


async def test_provider_error_posts_notice_without_crashing(setup):
    make, store, webhooks, channel = setup
    orch, _ = make(ProviderError("RateLimitError 429"))
    await orch.run(channel, human("hi"))
    channel.send.assert_awaited_once_with("Claude is unavailable right now.")
    assert len(await store.recent(1, 1000)) == 1


async def test_record_appends_without_reply(setup):
    make, store, webhooks, channel = setup
    orch, provider = make()
    await orch.record(human("over cap"))
    assert provider.calls == []
    assert [m.text for m in await store.recent(1, 1000)] == ["over cap"]
