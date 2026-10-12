"""Transcript data model."""

import time
from dataclasses import dataclass, field

# Persona key -> display name.
AI_NAMES = {"claude": "Claude", "gpt": "GPT"}


@dataclass(frozen=True)
class TranscriptMessage:
    channel_id: int
    speaker: str  # "human" or a key of AI_NAMES
    text: str
    display_name: str = ""
    author_id: int | None = None
    discord_message_id: int | None = None
    timestamp: float = field(default_factory=time.time)
    id: int | None = None
