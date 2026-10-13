import base64
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

import attachments
from attachments import Expired, ImageLoader, encode, from_discord
from store import Store
from transcript import Attachment, ImageData, TranscriptMessage


def png(size=(10, 10), mode="RGB", color="red") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def decode(image: ImageData) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image.data)))


# Ingest

@pytest.mark.parametrize(
    "filename, content_type, kind",
    [("a.png", "image/png", "image"), ("a.JPG", None, "image"), ("x", "image/webp; q=1", "image"), ("a.txt", "text/plain", "other")],
)
def test_kind(filename, content_type, kind):
    att = from_discord(SimpleNamespace(filename=filename, content_type=content_type, url="u"))
    assert att.kind == kind and att.url == "u"


# Encoding

def test_downscales_long_edge_to_jpeg():
    image = encode(png((3000, 1000)), 1568)
    assert image.media_type == "image/jpeg" and decode(image).size == (1568, 523)


def test_transparent_stays_png():
    image = encode(png(mode="RGBA", color=(0, 0, 0, 0)), 1568)
    assert image.media_type == "image/png" and decode(image).mode == "RGBA"


def test_transparent_flattened_on_white_when_png_too_big(monkeypatch):
    # Noise under full transparency: a big PNG, but a plain white JPEG once flattened.
    noise = Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).convert("RGBA")
    noise.putalpha(0)
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")
    monkeypatch.setattr(attachments, "MAX_ENCODED", 2000)
    image = encode(buffer.getvalue(), 1568)
    assert image.media_type == "image/jpeg" and decode(image).getpixel((5, 5))[0] > 240


def test_unreadable_is_none():
    assert encode(b"not an image", 1568) is None


# Loader

@pytest.fixture
async def store(tmp_path):
    store = await Store.open(tmp_path / "t.sqlite")
    yield store
    await store.close()


def image_msg(store_id, *names, discord_id=100):
    atts = tuple(Attachment(n, "image/png", "image", f"https://cdn/{n}") for n in names)
    return TranscriptMessage(channel_id=1, speaker="human", text="", display_name="W",
                             discord_message_id=discord_id, attachments=atts, id=store_id)


async def test_only_last_three_image_messages_loaded(store):
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(return_value=png())
    text_only = TranscriptMessage(channel_id=1, speaker="human", text="t", id=9,
                                  attachments=(Attachment("a.txt", "text/plain", "other", "u"),))
    transcript = [image_msg(i, "a.png") for i in range(1, 5)] + [text_only]
    images = await loader.load(MagicMock(), transcript)
    assert sorted(images) == [(2, 0), (3, 0), (4, 0)]
    assert all(isinstance(v, ImageData) for v in images.values())


async def test_cached_across_turns(store):
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(return_value=png())
    for _ in range(2):
        await loader.load(MagicMock(), [image_msg(1, "a.png")])
    loader._download.assert_awaited_once()


async def test_unsupported_format_note(store):
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(return_value=b"heic bytes")
    assert await loader.load(MagicMock(), [image_msg(1, "a.heic")]) == {(1, 0): "unsupported format"}


async def test_expired_url_refreshed_once_and_persisted(store):
    stored = await store.append(image_msg(None, "a.png", "b.png"))
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(side_effect=[Expired, png()])
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
        attachments=[SimpleNamespace(url="https://cdn/a2"), SimpleNamespace(url="https://cdn/b2")]))
    images = await loader._image(channel, stored, 0)
    assert isinstance(images, ImageData)
    assert loader._download.await_args_list[1].args == ("https://cdn/a2",)
    assert [a.url for a in (await store.recent(1, 1000))[0].attachments] == ["https://cdn/a2", "https://cdn/b2"]


async def test_second_failure_is_unavailable(store):
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(side_effect=[Expired, Expired])
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(attachments=[SimpleNamespace(url="https://cdn/a2")]))
    assert await loader._image(channel, image_msg(1, "a.png"), 0) == "unavailable"


async def test_deleted_message_is_unavailable(store):
    loader = ImageLoader(store, 1568)
    loader._download = AsyncMock(side_effect=Expired)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    assert await loader._image(channel, image_msg(1, "a.png"), 0) == "unavailable"
