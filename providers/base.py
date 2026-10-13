"""Provider interface and the transcript conversion shared by all providers."""

import re
import string
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Protocol

from transcript import AI_NAMES, Attachment, ImageData, TranscriptMessage

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

    async def respond(
        self,
        system: str,
        transcript: list[TranscriptMessage],
        images: Mapping[tuple[int, int], ImageData | str],
        hint: str,
    ) -> Reply: ...


def label(msg: TranscriptMessage) -> str:
    """Speaker name as the AIs see it. Humans can't pose as an AI or the system."""
    if msg.speaker in AI_NAMES:
        return AI_NAMES[msg.speaker]
    name = re.sub(r"[\[\]\r\n]", "", msg.display_name).strip() or "Someone"
    return f"{name} (human)" if name.lower() in RESERVED_NAMES else name


def attachment_note(att: Attachment, note: str | None = None) -> str:
    """Text stand-in for an attachment that is not sent as an image."""
    name = re.sub(r"[\[\]\r\n]", "", att.filename)
    if att.kind != "image":
        return f"[file: {name} - not supported yet]"
    return f"[image: {name} - {note}]" if note else f"[image: {name}]"


def to_messages(
    transcript: list[TranscriptMessage],
    persona: str,
    images: Mapping[tuple[int, int], ImageData | str] | None = None,
    image_block: Callable[[ImageData], dict] | None = None,
) -> list[dict]:
    """Own messages become assistant turns; everyone else's are labeled and merged into user turns.

    images: keyed by (message id, attachment index); ImageData goes out via image_block, a str is a
    note for the placeholder. Images not in the map become plain placeholders.
    """
    images = images or {}
    turns: list[tuple[str, list]] = []
    for msg in transcript:
        if msg.speaker == persona:
            role, parts = "assistant", [msg.text]
        else:
            role, parts = "user", [f"[{label(msg)}]: {msg.text}"]
            for i, att in enumerate(msg.attachments):
                image = images.get((msg.id, i))
                if isinstance(image, ImageData):
                    parts.append(image_block(image))
                else:
                    _add(parts, attachment_note(att, image), "\n")
        if turns and turns[-1][0] == role:
            _add(turns[-1][1], parts[0], "\n\n")
            turns[-1][1].extend(parts[1:])
        else:
            turns.append((role, parts))
    # The API requires a user turn first.
    while turns and turns[0][0] == "assistant":
        turns.pop(0)
    return [{"role": role, "content": _content(parts)} for role, parts in turns]


def add_hint(messages: list[dict], hint: str) -> None:
    """Turn hint as the final user content; never stored in the transcript."""
    text = f"[System]: {hint}"
    if messages and messages[-1]["role"] == "user":
        content = messages[-1]["content"]
        messages[-1]["content"] = (
            f"{content}\n\n{text}" if isinstance(content, str) else [*content, {"type": "text", "text": text}]
        )
    else:
        messages.append({"role": "user", "content": text})


def _add(parts: list, part: str | dict, sep: str) -> None:
    """Append, joining text onto a trailing text part."""
    if isinstance(part, str) and parts and isinstance(parts[-1], str):
        parts[-1] += sep + part
    else:
        parts.append(part)


def _content(parts: list) -> str | list[dict]:
    if len(parts) == 1 and isinstance(parts[0], str):
        return parts[0]
    return [{"type": "text", "text": p} if isinstance(p, str) else p for p in parts]


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
    # Plain hyphens instead of em dashes; line breaks around them stay.
    text = re.sub(r"[ \t]*—[ \t]*", " - ", text)
    return text + " […]" if reply.stop == "truncated" else text
