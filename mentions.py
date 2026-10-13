"""Who a message addresses with plain-text @mentions."""

import re

from providers.base import RESERVED_NAMES
from transcript import AI_NAMES, TranscriptMessage

# Fenced blocks (also unclosed at the end) and inline code; decorators and the like don't address anyone.
CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)


def _pattern(alias: str) -> re.Pattern:
    return re.compile(rf"(?<!\w)@{re.escape(alias)}(?!\w)", re.IGNORECASE)


# Persona key -> patterns for its name and family alias (@GPT also addresses Quill).
AI_PATTERNS = {persona: [_pattern(alias) for alias in {name, persona}] for persona, name in AI_NAMES.items()}


def mentions(msg: TranscriptMessage, transcript: list[TranscriptMessage]) -> set[str]:
    """Persona keys and human display names addressed by msg, without the speaker itself."""
    text = CODE.sub(" ", msg.text)
    found = {persona for persona, patterns in AI_PATTERNS.items() if any(p.search(text) for p in patterns)}
    humans = {m.display_name for m in transcript if m.speaker == "human" and m.display_name}
    found |= {name for name in humans if name.lower() not in RESERVED_NAMES and _pattern(name).search(text)}
    found.discard(msg.speaker if msg.speaker != "human" else msg.display_name)
    return found


def mention_names(targets: set[str]) -> str:
    """`@Quill, @Walrus` in a stable order."""
    return ", ".join(sorted(f"@{AI_NAMES.get(t, t)}" for t in targets))
