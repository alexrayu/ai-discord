"""Persona posting through bot-owned webhooks."""

import asyncio
from pathlib import Path

import discord

from models import AI_NAMES

AVATAR_DIR = Path(__file__).parent / "avatars"
AVATAR_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "gif")
MESSAGE_LIMIT = 2000
FENCE = "```"


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split at paragraph, then line boundaries; code fences stay balanced in every part."""
    if len(text) <= limit:
        return [text]

    # Room for a reopened fence header at the start and a closing fence at the end.
    openers = [line.strip() for line in text.splitlines() if line.lstrip().startswith(FENCE)]
    budget = limit - max((len(o) for o in openers), default=0) - len(FENCE) - 2

    raw: list[str] = []
    while len(text) > budget:
        window = text[:budget]
        cut = window.rfind("\n\n")
        if cut < budget // 2:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = budget
        raw.append(text[:cut])
        text = text[cut:].lstrip("\n")
    raw.append(text)

    # Close a fence left open at the end of a part and reopen it in the next.
    parts: list[str] = []
    fence: str | None = None
    for chunk in raw:
        head = f"{fence}\n" if fence else ""
        for line in chunk.splitlines():
            if line.lstrip().startswith(FENCE):
                fence = None if fence else line.strip()
        tail = f"\n{FENCE}" if fence else ""
        parts.append(head + chunk + tail)
    return parts


class Webhooks:
    """One bot-owned webhook per persona and channel, avatar uploaded from avatars/<persona>.*."""

    def __init__(self, client: discord.Client, avatar_dir: Path = AVATAR_DIR):
        self._client = client
        self._avatars = {persona: _read_avatar(avatar_dir, persona) for persona in AI_NAMES}
        self._cache: dict[tuple[int, str], discord.Webhook] = {}
        self._create_lock = asyncio.Lock()

    async def send(self, channel: discord.abc.Messageable, persona: str, text: str) -> None:
        if isinstance(channel, discord.Thread):
            parent, thread = channel.parent, channel
        else:
            parent, thread = channel, discord.utils.MISSING
        for part in split_message(text):
            await self._send_part(parent, thread, persona, part)

    async def _send_part(self, parent, thread, persona, text) -> None:
        # A webhook deleted on Discord's side is recreated once.
        for attempt in range(2):
            hook = await self._get(parent, persona)
            try:
                await hook.send(text, thread=thread, allowed_mentions=discord.AllowedMentions.none())
                return
            except discord.NotFound:
                self._cache.pop((parent.id, persona), None)
                if attempt:
                    raise

    async def _get(self, channel, persona: str) -> discord.Webhook:
        key = (channel.id, persona)
        if hook := self._cache.get(key):
            return hook
        async with self._create_lock:
            if hook := self._cache.get(key):
                return hook
            name, avatar, own_id = AI_NAMES[persona], self._avatars[persona], self._client.user.id
            hook = next(
                (h for h in await channel.webhooks() if h.name == name and h.user and h.user.id == own_id),
                None,
            )
            if hook is None:
                hook = await channel.create_webhook(name=name, avatar=avatar)
            elif avatar:
                # Once per process start, so a replaced avatar file takes effect after a restart.
                hook = await hook.edit(avatar=avatar)
            self._cache[key] = hook
            return hook


def _read_avatar(avatar_dir: Path, persona: str) -> bytes | None:
    for ext in AVATAR_EXTENSIONS:
        path = avatar_dir / f"{persona}.{ext}"
        if path.is_file():
            return path.read_bytes()
    return None
