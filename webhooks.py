"""Persona posting through one bot-owned webhook per channel."""

import asyncio

import discord

WEBHOOK_NAME = "AI Chat"
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
    def __init__(self, client: discord.Client):
        self._client = client
        self._cache: dict[int, discord.Webhook] = {}
        self._create_lock = asyncio.Lock()

    async def send(
        self,
        channel: discord.abc.Messageable,
        username: str,
        text: str,
        avatar_url: str | None = None,
    ) -> None:
        if isinstance(channel, discord.Thread):
            parent, thread = channel.parent, channel
        else:
            parent, thread = channel, discord.utils.MISSING
        for part in split_message(text):
            await self._send_part(parent, thread, username, part, avatar_url)

    async def _send_part(self, parent, thread, username, text, avatar_url) -> None:
        # A webhook deleted on Discord's side is recreated once.
        for attempt in range(2):
            hook = await self._get(parent)
            try:
                await hook.send(
                    text,
                    username=username,
                    avatar_url=avatar_url or discord.utils.MISSING,
                    thread=thread,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.NotFound:
                self._cache.pop(parent.id, None)
                if attempt:
                    raise

    async def _get(self, channel) -> discord.Webhook:
        hook = self._cache.get(channel.id)
        if hook:
            return hook
        async with self._create_lock:
            hook = self._cache.get(channel.id)
            if hook:
                return hook
            own_id = self._client.user.id
            hook = next(
                (
                    h for h in await channel.webhooks()
                    if h.name == WEBHOOK_NAME and h.user and h.user.id == own_id
                ),
                None,
            ) or await channel.create_webhook(name=WEBHOOK_NAME)
            self._cache[channel.id] = hook
            return hook
