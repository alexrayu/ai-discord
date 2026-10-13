"""Turn handling per channel or thread."""

import asyncio
import logging
import random
from collections import defaultdict

import discord

from attachments import ImageLoader
from mentions import mention_names, mentions
from providers.base import Provider, ProviderError, finalize
from store import Store
from transcript import AI_NAMES, TranscriptMessage
from webhooks import Webhooks

log = logging.getLogger(__name__)

YOUR_TURN = "Your turn. Reply or PASS."
ADDRESSED = "You were addressed directly. Reply; PASS is not allowed."
MUST_REPLY = "You must reply. A few words are enough."


class Orchestrator:
    def __init__(
        self,
        store: Store,
        webhooks: Webhooks,
        images: ImageLoader,
        providers: dict[str, Provider],
        prompts: dict[str, str],
        char_limit: int,
        budget: int,
    ):
        self._store = store
        self._webhooks = webhooks
        self._images = images
        self._providers = providers
        self.prompts = prompts
        self._char_limit = char_limit
        self._budget = budget
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def record(self, msg: TranscriptMessage) -> None:
        """Append without triggering a reply."""
        async with self._locks[msg.channel_id]:
            await self._store.append(msg)

    async def run(self, channel: discord.abc.Messageable, msg: TranscriptMessage) -> None:
        """Append a human message, then run rounds until both AIs pass or the turn budget is spent."""
        async with self._locks[msg.channel_id]:
            await self._store.append(msg)
            transcript = await self._store.recent(channel.id, self._char_limit)
            # AIs that were addressed and haven't answered yet; they go first and may not PASS.
            owed = mentions(transcript[-1], transcript) & AI_NAMES.keys()
            turns = 0
            while True:
                queue = list(self._providers)
                random.shuffle(queue)
                spoke = False
                while queue:
                    queue.sort(key=lambda p: p not in owed)
                    persona = queue.pop(0)
                    must = persona in owed
                    owed.discard(persona)
                    reply = await self._turn(channel, persona, must)
                    if reply is None:
                        continue
                    spoke = True
                    turns += 1
                    if turns >= self._budget:
                        return
                    owed |= mentions(reply, transcript) & AI_NAMES.keys()
                if not spoke:
                    return

    async def _turn(self, channel, persona: str, must: bool) -> TranscriptMessage | None:
        """One AI request (plus the must-reply retry); the posted message, or None if nothing was posted."""
        transcript = await self._store.recent(channel.id, self._char_limit)
        name = AI_NAMES[persona]
        provider = self._providers[persona]
        hints = [ADDRESSED, MUST_REPLY] if must else [self._hint(persona, transcript)]
        text = None
        try:
            async with channel.typing():
                images = await self._images.load(channel, transcript)
                for hint in hints:
                    reply = await provider.respond(self.prompts[persona], transcript, images, hint)
                    if reply.stop == "refusal":
                        await channel.send(f"{name} declined to answer.")
                        return None
                    if (text := finalize(reply, persona, transcript)) is not None:
                        break
        except ProviderError as e:
            log.error("%s request failed: %s", name, e)
            await channel.send(f"{name} is unavailable right now.")
            return None
        if text is None:
            return None
        await self._webhooks.send(channel, persona, text)
        return await self._store.append(
            TranscriptMessage(channel_id=channel.id, speaker=persona, text=text, display_name=name)
        )

    @staticmethod
    def _hint(persona: str, transcript: list[TranscriptMessage]) -> str:
        """Newest addressing since the last human message; a plain reply doesn't open the floor again."""
        for msg in reversed(transcript):
            if targets := mentions(msg, transcript) - {persona}:
                return f"This was addressed to {mention_names(targets)}. PASS unless you have a correction or a real objection."
            if msg.speaker == "human":
                break
        return YOUR_TURN
