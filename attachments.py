"""Attachment ingest and on-demand image loading. Image bytes stay in memory, never on disk."""

import asyncio
import base64
import io
import logging
from collections import OrderedDict
from dataclasses import replace
from pathlib import PurePath

import aiohttp
import discord
from PIL import Image, ImageOps, UnidentifiedImageError

from store import Store
from transcript import Attachment, ImageData, TranscriptMessage

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
IMAGE_WINDOW = 3  # newest messages with images that carry bytes
MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_ENCODED = 3_750_000  # base64 of this stays under the 5 MB per-image API limit
CACHE_BYTES = 100 * 1024 * 1024
UNSUPPORTED = "unsupported format"
UNAVAILABLE = "unavailable"


class Expired(Exception):
    """Signed CDN URL no longer valid."""


class TooLarge(Exception):
    pass


def from_discord(att: discord.Attachment) -> Attachment:
    media_type = (att.content_type or "").split(";")[0].strip().lower()
    is_image = media_type.startswith("image/") or PurePath(att.filename).suffix.lower() in IMAGE_EXTENSIONS
    return Attachment(att.filename, media_type, "image" if is_image else "other", att.url)


def encode(raw: bytes, max_edge: int) -> ImageData | None:
    """Downscale to max_edge; PNG when transparent, else JPEG; None if unreadable."""
    try:
        with Image.open(io.BytesIO(raw)) as source:
            img = ImageOps.exif_transpose(source)
            img.thumbnail((max_edge, max_edge))
            if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
                rgba = img.convert("RGBA")
                if len(png := _save(rgba, "PNG")) <= MAX_ENCODED:
                    return ImageData("image/png", base64.b64encode(png).decode())
                # Flatten on white: dropping alpha alone turns transparent areas black.
                img = Image.new("RGB", rgba.size, "white")
                img.paste(rgba, mask=rgba.getchannel("A"))
            rgb = img.convert("RGB")
            for quality in (90, 75, 60):
                if len(jpeg := _save(rgb, "JPEG", quality=quality)) <= MAX_ENCODED:
                    return ImageData("image/jpeg", base64.b64encode(jpeg).decode())
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        pass
    return None


def _save(img: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, fmt, optimize=True, **options)
    return buffer.getvalue()


class ImageLoader:
    def __init__(self, store: Store, max_edge: int):
        self._store = store
        self._max_edge = max_edge
        self._session: aiohttp.ClientSession | None = None
        # LRU of encoded images (or the UNSUPPORTED note), keyed by (message id, attachment index).
        self._cache: OrderedDict[tuple[int, int], ImageData | str] = OrderedDict()
        self._cache_bytes = 0

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def load(
        self, channel: discord.abc.Messageable, transcript: list[TranscriptMessage]
    ) -> dict[tuple[int, int], ImageData | str]:
        """Image data or a note for every image in the newest IMAGE_WINDOW messages that have images."""
        recent = [m for m in transcript if any(a.kind == "image" for a in m.attachments)][-IMAGE_WINDOW:]
        return {
            (msg.id, i): await self._image(channel, msg, i)
            for msg in recent
            for i, att in enumerate(msg.attachments)
            if att.kind == "image"
        }

    async def _image(self, channel, msg: TranscriptMessage, index: int) -> ImageData | str:
        key = (msg.id, index)
        if (hit := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            return hit
        raw = await self._fetch(channel, msg, index)
        if raw is None:
            return UNAVAILABLE  # not cached: may be transient
        result = await asyncio.to_thread(encode, raw, self._max_edge) or UNSUPPORTED
        self._remember(key, result)
        return result

    def _remember(self, key: tuple[int, int], value: ImageData | str) -> None:
        self._cache[key] = value
        self._cache_bytes += len(value.data) if isinstance(value, ImageData) else 0
        while self._cache_bytes > CACHE_BYTES:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= len(old.data) if isinstance(old, ImageData) else 0

    async def _fetch(self, channel, msg: TranscriptMessage, index: int) -> bytes | None:
        """Download; an expired URL is refreshed from the Discord message once."""
        try:
            try:
                return await self._download(msg.attachments[index].url)
            except Expired:
                url = await self._refresh(channel, msg, index)
                return await self._download(url) if url else None
        except (Expired, TooLarge, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Image %s/%s unavailable: %s", msg.id, index, type(e).__name__)
            return None

    async def _download(self, url: str) -> bytes:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        async with self._session.get(url) as response:
            if response.status in (403, 404):
                raise Expired
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(1 << 16):
                data += chunk
                if len(data) > MAX_DOWNLOAD:
                    raise TooLarge
            return bytes(data)

    async def _refresh(self, channel, msg: TranscriptMessage, index: int) -> str | None:
        """Fresh signed URLs from the original message, persisted for later turns."""
        if msg.discord_message_id is None:
            return None
        try:
            fresh = await channel.fetch_message(msg.discord_message_id)
        except discord.HTTPException:
            return None
        if len(fresh.attachments) != len(msg.attachments):
            return None
        attachments = tuple(replace(a, url=f.url) for a, f in zip(msg.attachments, fresh.attachments))
        await self._store.update_attachments(msg.id, attachments)
        return attachments[index].url
