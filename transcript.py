"""Transcript data model."""

import time
from dataclasses import dataclass, field

# Persona key (model family) -> name shown in Discord and to the AIs.
AI_NAMES = {"claude": "Claude", "gpt": "Quill"}


@dataclass(frozen=True)
class Attachment:
    filename: str
    media_type: str
    kind: str  # "image" | "text" | "pdf" | "other"
    url: str
    text: str | None = None  # text files only: content read once at ingest


@dataclass(frozen=True)
class TranscriptMessage:
    channel_id: int
    speaker: str  # "human" or a key of AI_NAMES
    text: str
    display_name: str = ""
    author_id: int | None = None
    discord_message_id: int | None = None
    attachments: tuple[Attachment, ...] = ()
    timestamp: float = field(default_factory=time.time)
    id: int | None = None


@dataclass(frozen=True)
class ImageData:
    """Downscaled image ready for a provider request; never stored."""

    media_type: str
    data: str  # base64
