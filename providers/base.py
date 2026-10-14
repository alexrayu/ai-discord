"""Provider interface and the request building shared by all providers."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from models import ImageData, Part, PdfData


class ProviderError(Exception):
    """A request failed after the SDK's own retries."""

    def __init__(self, message: str, out_of_credits: bool = False):
        super().__init__(message)
        self.out_of_credits = out_of_credits


@dataclass(frozen=True)
class Reply:
    text: str
    stop: str  # "end" | "truncated" | "refusal"


class Provider(Protocol):
    name: str

    async def respond(self, system: str, material: list[Part], turns: list[tuple[str, str]]) -> Reply: ...


def to_messages(
    material: list[Part],
    turns: list[tuple[str, str]],
    block: Callable[[ImageData | PdfData], dict],
    cache: dict | None = None,
) -> list[dict]:
    """The material opens the first user message, followed by the (role, text) turns.

    cache marks the last material block, so every step of a review shares the cached material prefix.
    """
    content = [{"type": "text", "text": p} if isinstance(p, str) else block(p) for p in material]
    if cache:
        content[-1] = {**content[-1], **cache}
    messages = [{"role": "user", "content": content}]
    for role, text in turns:
        part = {"type": "text", "text": text}
        if messages[-1]["role"] == role:
            messages[-1]["content"].append(part)
        else:
            messages.append({"role": role, "content": [part]})
    return messages


def finalize(reply: Reply) -> str | None:
    """Text to post, or None for a refusal or an empty reply."""
    if reply.stop == "refusal":
        return None
    # Plain hyphens instead of em dashes; line breaks around them stay.
    text = re.sub(r"[ \t]*—[ \t]*", " - ", reply.text).strip()
    if not text:
        return None
    return text + " […]" if reply.stop == "truncated" else text
