"""Data model shared by attachments, providers and the review pipeline."""

from dataclasses import dataclass

# Persona key (model family) -> name shown in Discord and to the AIs.
AI_NAMES = {"claude": "Claude", "gpt": "Quill"}


@dataclass(frozen=True)
class ImageData:
    """Downscaled image ready for a provider request; never stored."""

    media_type: str
    data: str  # base64


@dataclass(frozen=True)
class PdfData:
    """PDF passed to the providers as is; never stored."""

    filename: str
    data: str  # base64


# One piece of review material: text (labels, inlined scripts, notes), an image or a PDF.
Part = str | ImageData | PdfData
