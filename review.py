"""Three-step review of one upload (Claude analyzes, Quill critiques, Claude writes the final review), and follow-up answers in its thread."""

import logging
import random
import re
import traceback
from dataclasses import dataclass, field

import discord

from attachments import collect, has_content, pages
from models import AI_NAMES
from providers.base import Provider, ProviderError, finalize
from webhooks import Webhooks

log = logging.getLogger(__name__)

NOTE_MAX_CHARS = 4000
HISTORY_LIMIT = 500  # thread messages scanned for the review and the follow-ups
TITLES = {"analysis": "Analysis", "critique": "Critique", "final": "Final review"}
AUTHORS = {"analysis": "claude", "critique": "gpt", "final": "claude"}
HEADERS = {step: f"**{title}**\n\n" for step, title in TITLES.items()}
TALK_LIMIT = 20  # earlier follow-up messages given as context
MAX_CITED = 5  # page images attached to one follow-up
QUILL = re.compile(r"(?<!\w)@(quill|gpt)\b", re.IGNORECASE)
# "p. 4", "pp. 3-5", "page 12", "pages 3 to 5"
CITED = re.compile(r"(?<!\w)(?:pp?|pages?|pg)\.?\s*(\d{1,3})(?:\s*(?:-|–|to)\s*(\d{1,3}))?", re.IGNORECASE)
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


@dataclass
class ThreadState:
    steps: dict[str, str] = field(default_factory=dict)  # newest run; complete when it has "final"
    talk: list[tuple[str, str, str, int]] = field(default_factory=list)  # (speaker, name, text, message id) after the final review


def _speaker(msg: discord.Message) -> str | None:
    """'human', a persona key, or None for the bot's own notices."""
    if msg.webhook_id is None:
        return None if msg.author.bot else "human"
    return next((key for key, name in AI_NAMES.items() if msg.author.name == name), None)


async def read_thread(thread: discord.Thread) -> ThreadState:
    """The newest review, its split posts rejoined, and the follow-up conversation after it."""
    state = ThreadState()
    current = None  # step whose continuation parts may follow
    async for msg in thread.history(limit=HISTORY_LIMIT, oldest_first=True):
        speaker = _speaker(msg)
        step = next((s for s, h in HEADERS.items() if msg.content.startswith(h)), None)
        if step and speaker == AUTHORS[step]:
            if step == "analysis":
                state = ThreadState()  # a later run replaces the earlier one
            state.steps[step], current = msg.content.removeprefix(HEADERS[step]), step
        elif current and speaker == AUTHORS[current]:
            state.steps[current] += "\n\n" + msg.content
        else:
            current = None
            if "final" in state.steps and speaker:
                state.talk.append((speaker, msg.author.display_name, msg.clean_content, msg.id))
    return state


async def final_review(thread: discord.Thread) -> str | None:
    return (await read_thread(thread)).steps.get("final")


def cited_pages(text: str) -> list[int]:
    """Page numbers named in a question, ranges expanded, in order of first mention."""
    found: list[int] = []
    for match in CITED.finditer(text):
        start = int(match[1])
        end = int(match[2]) if match[2] and int(match[2]) >= start else start
        found += [n for n in range(start, min(end, start + MAX_CITED) + 1) if n not in found]
    return found


def _label(name: str) -> str:
    return re.sub(r"[\[\]\r\n]", "", name).strip() or "Someone"


class StepFailed(Exception):
    """A step produced nothing to post; the notice is already in the thread."""


class Reviewer:
    def __init__(self, webhooks: Webhooks, providers: dict[str, Provider], prompts: dict[str, str], image_max_edge: int):
        self._webhooks = webhooks
        self._providers = providers
        self.prompts = prompts
        self._image_max_edge = image_max_edge

    async def run(self, thread: discord.Thread, upload: discord.Message) -> bool:
        """Post the three steps into the thread. Errors end up as a notice in the thread, never raised."""
        try:
            await self._steps(thread, upload)
            return True
        except StepFailed:
            pass
        except discord.HTTPException as e:
            log.error("Discord request failed in thread %s: %s %s", thread.id, e.status, e.code)
        except Exception as e:
            # Type and stack only: exception messages may quote the material.
            log.error("Review failed in thread %s: %s\n%s", thread.id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
            await thread.send("The review failed.")
        return False

    async def followup(self, thread: discord.Thread, upload: discord.Message | None, question: discord.Message) -> None:
        """One answer to a member's message after the review; by Claude, by Quill when @mentioned. Never raises."""
        try:
            await self._followup(thread, upload, question)
        except StepFailed:
            pass
        except discord.HTTPException as e:
            log.error("Discord request failed in thread %s: %s %s", thread.id, e.status, e.code)
        except Exception as e:
            log.error("Follow-up failed in thread %s: %s\n%s", thread.id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
            await thread.send("The answer failed.")

    async def _followup(self, thread, upload, question) -> None:
        prompts = self.prompts
        state = await read_thread(thread)
        if "final" not in state.steps:
            await thread.send("There is no finished review in this thread to ask about.")
            return
        persona = "gpt" if QUILL.search(question.content) else "claude"
        review = "\n\n".join(
            f"## {TITLES[step]} ({AI_NAMES[AUTHORS[step]]})\n\n{state.steps[step]}" for step in TITLES if step in state.steps
        )
        material = [f"The review of {thread.name}:\n\n{review}"]
        cited = cited_pages(question.content)
        if cited and upload:
            async with thread.typing():
                material += await pages(upload, self._image_max_edge, cited[:MAX_CITED])
            if len(cited) > MAX_CITED:
                material.append(f"[Only the first {MAX_CITED} cited pages are attached.]")

        # Own earlier answers are assistant turns, everyone else's are labeled user turns.
        turns = [
            ("assistant", text) if speaker == persona else ("user", f"[{_label(name)}]: {text}")
            for speaker, name, text, msg_id in state.talk[-TALK_LIMIT - 1:] if msg_id != question.id
        ][-TALK_LIMIT:]
        turns.append(("user", f"[{_label(question.author.display_name)}]: {question.clean_content}\n\n{prompts['followup']}"))
        await self._post(thread, prompts, persona, material, turns, retry_hint="Ask again to retry.")

    async def _steps(self, thread: discord.Thread, upload: discord.Message) -> None:
        prompts = self.prompts  # one set for the whole review, even if !reload lands meanwhile
        claude, quill = AI_NAMES["claude"], AI_NAMES["gpt"]
        async with thread.typing():
            material = await collect(upload, self._image_max_edge)
        if not has_content(material):
            await thread.send("Nothing in the upload could be read: " + " ".join(material))
            raise StepFailed

        # Claude's second request repeats the first one's prefix, so the material is read from cache.
        analysis = await self._step(thread, prompts, "claude", "analysis", material, [("user", prompts["analysis"])])
        critique = await self._step(
            thread, prompts, "gpt", "critique", material,
            [("user", f"{claude}'s analysis:\n\n{analysis}\n\n{prompts['critique']}")],
        )
        note = upload.clean_content.strip()[:NOTE_MAX_CHARS]
        note = f"Author's note on intent:\n{note}" if note else "No author's note on intent was given."
        await self._step(thread, prompts, "claude", "final", material, [
            ("user", prompts["analysis"]),
            ("assistant", analysis),
            ("user", f"{quill}'s critique:\n\n{critique}\n\n{note}\n\n{prompts['final']}"),
        ])

    async def _step(self, thread, prompts: dict[str, str], persona: str, step: str, material, turns) -> str:
        return await self._post(thread, prompts, persona, material, turns, header=HEADERS[step])

    async def _post(self, thread, prompts, persona: str, material, turns, header: str = "",
                    retry_hint: str = "Press the button again to retry.") -> str:
        """One request, posted by the persona; a failure leaves a notice in the thread and raises StepFailed."""
        name = AI_NAMES[persona]
        try:
            async with thread.typing():
                reply = await self._providers[persona].respond(prompts[persona], material, turns)
        except ProviderError as e:
            log.error("%s request failed: %s", name, e)
            notice = random.choice(OUT_OF_CREDITS) if e.out_of_credits else "{name} is unavailable right now."
            await thread.send(f"{notice.format(name=name)} {retry_hint}")
            raise StepFailed from e
        if (text := finalize(reply)) is None:
            await thread.send(f"{name} declined to answer." if reply.stop == "refusal" else f"{name} returned nothing.")
            raise StepFailed
        await self._webhooks.send(thread, persona, header + text)
        return text
