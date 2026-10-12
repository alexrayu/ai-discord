"""Provider interface and the transcript conversion shared by all providers."""

import re
import string
from dataclasses import dataclass
from typing import Protocol

from transcript import AI_NAMES, TranscriptMessage

# Humans may not use an AI name, a model family name or "System".
RESERVED_NAMES = {name.lower() for name in AI_NAMES.values()} | set(AI_NAMES) | {"system"}
PASS_STRIP = string.whitespace + string.punctuation + "“”‘’«»"


class ProviderError(Exception):
    """A request failed after the SDK's own retries."""


@dataclass(frozen=True)
class Reply:
    text: str
    stop: str  # "end" | "truncated" | "refusal"


class Provider(Protocol):
    name: str

    async def respond(self, system: str, transcript: list[TranscriptMessage]) -> Reply: ...


def label(msg: TranscriptMessage) -> str:
    """Speaker name as the AIs see it. Humans can't pose as an AI or the system."""
    if msg.speaker in AI_NAMES:
        return AI_NAMES[msg.speaker]
    name = re.sub(r"[\[\]\r\n]", "", msg.display_name).strip() or "Someone"
    return f"{name} (human)" if name.lower() in RESERVED_NAMES else name


def to_messages(transcript: list[TranscriptMessage], persona: str) -> list[dict]:
    """Own messages become assistant turns; everyone else's are labeled and merged into user turns."""
    messages: list[dict] = []
    for msg in transcript:
        if msg.speaker == persona:
            role, text = "assistant", msg.text
        else:
            role, text = "user", f"[{label(msg)}]: {msg.text}"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})
    # The API requires a user turn first.
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    return messages


def finalize(reply: Reply, persona: str, transcript: list[TranscriptMessage]) -> str | None:
    """Text to post, or None for PASS."""
    if reply.stop == "refusal":
        return None
    own = re.escape(AI_NAMES[persona])
    text = re.sub(rf"^\s*\[?{own}\]?\s*:\s*", "", reply.text, flags=re.IGNORECASE)

    # Cut where the model starts writing as another participant.
    others = {label(m).lower() for m in transcript if m.speaker != persona}
    others |= {name.lower() for key, name in AI_NAMES.items() if key != persona}
    lines = text.split("\n")
    for i, line in enumerate(lines):
        match = re.match(r"\s*\[([^\]]+)\]\s*:", line)
        if match and match.group(1).strip().lower() in others:
            lines = lines[:i]
            break
    text = "\n".join(lines).strip()

    if text.strip(PASS_STRIP).upper() in ("", "PASS"):
        return None
    return text + " […]" if reply.stop == "truncated" else text
