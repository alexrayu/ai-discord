"""Turn handling per channel or thread."""

import asyncio
import logging
import random
import traceback
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
# Posted instead of the plain error notice when the provider account has run out of credits.
OUT_OF_CREDITS = (
    "{name} is sleeping and can't be bothered now.",
    "{name} went out for snacks and forgot the way back.",
    "{name} is on a coffee break that might last a while.",
    "{name} is touching grass. Try again later.",
    "{name} left to stare at a wall for a bit.",
    "{name} is out of juice. Someone feed the meter.",
    "{name} is AFK, probably napping on the keyboard.",
    "{name} is on strike until the snack budget is restored.",
    "{name} is meditating and refuses to come back yet.",
    "{name} ran off to binge manga. Back later, maybe.",
)


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
        self.default_budget = budget
        # control: orders human messages (cancel, append, restart); never held while awaiting an AI.
        # transcript: serializes appends; the only lock a round takes, so cancelling one can't deadlock.
        self._control: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._transcript: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rounds: dict[int, asyncio.Task] = {}

    async def record(self, msg: TranscriptMessage) -> None:
        """Append without triggering a reply or interrupting a running round."""
        await self._append(msg)

    async def run(self, channel: discord.abc.Messageable, msg: TranscriptMessage) -> asyncio.Task:
        """Stop the channel's running round, append the human message and start a new round."""
        async with self._control[msg.channel_id]:
            await self._cancel(msg.channel_id)
            await self._append(msg)
            task = asyncio.create_task(self._rounds_until_quiet(channel))
            self._rounds[msg.channel_id] = task
            return task

    async def reset(self, channel_id: int) -> None:
        """Stop the running round and clear the channel's transcript."""
        async with self._control[channel_id]:
            await self._cancel(channel_id)
            async with self._transcript[channel_id]:
                await self._store.clear(channel_id)

    async def _cancel(self, channel_id: int) -> None:
        """Cancel the running round and wait until it has finished cancelling. Caller holds the control lock."""
        if (running := self._rounds.get(channel_id)) and not running.done():
            running.cancel()
            await asyncio.wait({running})

    async def budget(self, channel_id: int) -> int:
        return await self._store.budget(channel_id) or self.default_budget

    async def _append(self, msg: TranscriptMessage) -> TranscriptMessage:
        async with self._transcript[msg.channel_id]:
            return await self._store.append(msg)

    async def _rounds_until_quiet(self, channel) -> None:
        """Rounds until both AIs pass or the turn budget is spent. Errors are logged, never raised."""
        try:
            await self._loop(channel)
        except asyncio.CancelledError:
            raise
        except discord.HTTPException as e:
            log.error("Discord request failed in channel %s: %s %s", channel.id, e.status, e.code)
        except Exception as e:
            # Type and stack only: exception messages may quote chat content.
            log.error("Round failed in channel %s: %s\n%s", channel.id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))

    async def _loop(self, channel) -> None:
        transcript = await self._store.recent(channel.id, self._char_limit)
        # AIs that were addressed and haven't answered yet; they go first and may not PASS.
        owed = mentions(transcript[-1], transcript) & AI_NAMES.keys()
        budget = await self.budget(channel.id)
        turns = 0
        failed: set[str] = set()  # one error notice per AI and run, not one per round
        while True:
            queue = [p for p in self._providers if p not in failed]
            random.shuffle(queue)
            spoke = False
            while queue:
                queue.sort(key=lambda p: p not in owed)
                persona = queue.pop(0)
                must = persona in owed
                owed.discard(persona)
                reply = await self._turn(channel, persona, must, failed)
                if reply is None:
                    continue
                spoke = True
                turns += 1
                if turns >= budget:
                    return
                owed |= mentions(reply, transcript) & AI_NAMES.keys()
            if not spoke:
                return

    async def _turn(self, channel, persona: str, must: bool, failed: set[str]) -> TranscriptMessage | None:
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
            failed.add(persona)
            notice = random.choice(OUT_OF_CREDITS) if e.out_of_credits else "{name} is unavailable right now."
            await channel.send(notice.format(name=name))
            return None
        if text is None:
            return None
        # Posting and recording run as their own task: a cancel landing here lets it finish, so a reply
        # is never half-posted, never posted unrecorded, and is recorded before the next round starts.
        post = asyncio.create_task(self._post(channel, persona, text))
        try:
            return await asyncio.shield(post)
        except asyncio.CancelledError:
            await post
            raise

    async def _post(self, channel, persona: str, text: str) -> TranscriptMessage:
        await self._webhooks.send(channel, persona, text)
        return await self._append(
            TranscriptMessage(channel_id=channel.id, speaker=persona, text=text, display_name=AI_NAMES[persona])
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
