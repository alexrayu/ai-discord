"""Turn handling per channel or thread."""

import asyncio
import logging
from collections import defaultdict

import discord

from providers.base import Provider, ProviderError, finalize
from store import Store
from transcript import AI_NAMES, TranscriptMessage
from webhooks import Webhooks

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        store: Store,
        webhooks: Webhooks,
        providers: dict[str, Provider],
        prompts: dict[str, str],
        char_limit: int,
    ):
        self._store = store
        self._webhooks = webhooks
        self._providers = providers
        self.prompts = prompts
        self._char_limit = char_limit
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def record(self, msg: TranscriptMessage) -> None:
        """Append without triggering a reply."""
        async with self._locks[msg.channel_id]:
            await self._store.append(msg)

    async def run(self, channel: discord.abc.Messageable, msg: TranscriptMessage) -> None:
        """Append a human message and let the AIs respond."""
        async with self._locks[msg.channel_id]:
            await self._store.append(msg)
            for persona, provider in self._providers.items():
                await self._turn(channel, persona, provider)

    async def _turn(self, channel, persona: str, provider: Provider) -> None:
        transcript = await self._store.recent(channel.id, self._char_limit)
        name = AI_NAMES[persona]
        try:
            async with channel.typing():
                reply = await provider.respond(self.prompts[persona], transcript)
        except ProviderError as e:
            log.error("%s request failed: %s", name, e)
            await channel.send(f"{name} is unavailable right now.")
            return

        if reply.stop == "refusal":
            await channel.send(f"{name} declined to answer.")
            return
        text = finalize(reply, persona, transcript)
        if text is None:
            return
        await self._webhooks.send(channel, persona, text)
        await self._store.append(
            TranscriptMessage(channel_id=channel.id, speaker=persona, text=text, display_name=name)
        )
