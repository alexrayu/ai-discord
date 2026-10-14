import base64
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

import attachments
from attachments import check, collect, encode, has_content, natural_key
from models import ImageData, PdfData


def png(size=(10, 10), mode="RGB", color="red") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def decode(image: ImageData) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image.data)))


# Kinds

@pytest.mark.parametrize(
    "filename, media_type, kind",
    [("a.png", "image/png", "image"), ("a.JPG", "", "image"), ("x", "image/webp", "image"), ("a.txt", "text/plain", "text"),
     ("main.py", "", "text"), ("data.json", "application/json", "text"), ("doc.pdf", "", "pdf"),
     ("x", "application/pdf", "pdf"), ("a.zip", "application/zip", "other")],
)
def test_kind(filename, media_type, kind):
    assert attachments.kind_of(filename, media_type) == kind


def discord_file(filename, content_type="", data=b"", size=None, error=None):
    return SimpleNamespace(filename=filename, content_type=content_type, size=len(data) if size is None else size,
                           read=AsyncMock(return_value=data, side_effect=error))


def upload(*files):
    return SimpleNamespace(attachments=list(files))


def test_check_usable_and_oversized():
    big = discord_file("ch.pdf", size=attachments.MAX_BYTES["pdf"] + 1)
    assert check(upload(discord_file("a.zip"), discord_file("p.png"))) == (True, [])
    assert check(upload(big, discord_file("a.zip", "application/zip"))) == (False, ["ch.pdf is 100.0 MB, max 100 MB"])
    assert check(upload()) == (False, [])


def test_natural_order():
    names = ["p10.png", "P2.png", "p1.png", "cover.png"]
    assert sorted(names, key=natural_key) == ["cover.png", "p1.png", "P2.png", "p10.png"]


# Material

def pdf_bytes(pages, size=(600, 850)) -> bytes:
    buffer = io.BytesIO()
    images = [Image.new("RGB", size, "white") for _ in range(pages)]
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


async def test_pages_numbered_in_filename_order():
    parts = await collect(upload(discord_file("p10.png", data=png()), discord_file("p2.png", data=png())), 1568)
    assert [p for p in parts if isinstance(p, str)] == ["Page 1 (p2.png):", "Page 2 (p10.png):"]
    assert isinstance(parts[1], ImageData) and isinstance(parts[3], ImageData)


async def test_unreadable_image_keeps_its_page_number():
    parts = await collect(upload(discord_file("a.png", data=b"x"), discord_file("b.png", data=png())), 1568)
    assert parts[0] == "[Page 1 (a.png): unreadable image]" and parts[1] == "Page 2 (b.png):"


async def test_pdf_passed_as_is():
    raw = pdf_bytes(2)
    parts = await collect(upload(discord_file("ch[1].pdf", data=raw)), 1568)
    assert parts == ["PDF: ch1.pdf", PdfData("ch1.pdf", base64.b64encode(raw).decode())]


async def test_long_pdf_rendered_instead_of_native(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_IMAGES", 2)
    parts = await collect(upload(discord_file("script.pdf", data=pdf_bytes(3))), 1568)
    assert not any(isinstance(p, PdfData) for p in parts)
    assert parts[-1] == "[script.pdf: pages 3-3 left out, 2 pages per review]"


async def test_oversized_pdf_not_downloaded():
    file = discord_file("big.pdf", size=attachments.MAX_BYTES["pdf"] + 1)
    assert await collect(upload(file), 1568) == ["[big.pdf: too large or unavailable]"]
    file.read.assert_not_called()


async def test_pdf_over_native_budget_rendered_to_pages(monkeypatch):
    monkeypatch.setattr(attachments, "NATIVE_PDF_MAX_BYTES", 10)
    parts = await collect(upload(discord_file("a.png", data=png()), discord_file("ch.pdf", data=pdf_bytes(2))), 1568)
    assert [p for p in parts if isinstance(p, str)] == ["Page 1 (a.png):", "Page 2 (ch.pdf, p. 1):", "Page 3 (ch.pdf, p. 2):"]
    assert decode(parts[3]).size == (1107, 1568)


async def test_page_cap_and_shared_budget(monkeypatch):
    monkeypatch.setattr(attachments, "NATIVE_PDF_MAX_BYTES", 10)
    monkeypatch.setattr(attachments, "MAX_IMAGES", 2)
    encode_calls = []
    original = attachments._encode
    monkeypatch.setattr(attachments, "_encode", lambda img, edge, budget: encode_calls.append(budget) or original(img, edge, budget))
    parts = await collect(upload(discord_file("ch.pdf", data=pdf_bytes(3)), discord_file("z.png", data=png())), 1568)
    assert parts[-2:] == ["[ch.pdf: pages 3-3 left out, 2 pages per review]", "[z.png: left out, 2 pages per review]"]
    assert encode_calls == [attachments.MAX_ENCODED] * 2


async def test_unreadable_pdf_is_a_note():
    assert await collect(upload(discord_file("x.pdf", data=b"not a pdf")), 1568) == [
        "[x.pdf: unreadable PDF or over the 100-page limit]"]


def test_has_content():
    assert has_content(["Page 1:", ImageData("image/png", "x")]) and has_content(["--- file: s.txt ---\nhi"])
    assert not has_content(["[ch.pdf: too large or unavailable]"])


async def test_script_inlined_and_truncated(monkeypatch):
    monkeypatch.setattr(attachments, "TEXT_MAX_CHARS", 5)
    parts = await collect(upload(discord_file("s.txt", "text/plain", "héllo world".encode())), 1568)
    assert parts == ["--- file: s.txt ---\nhéllo\n[truncated]"]


async def test_read_error_becomes_note_and_other_files_skipped():
    error = discord.NotFound(MagicMock(status=404), "gone")
    parts = await collect(upload(discord_file("s.md", error=error), discord_file("x.zip")), 1568)
    assert parts == ["[s.md: too large or unavailable]"]


# Encoding

def test_downscales_long_edge_to_jpeg():
    image = encode(png((3000, 1000)), 1568)
    assert image.media_type == "image/jpeg" and decode(image).size == (1568, 523)


def test_transparent_stays_png():
    image = encode(png(mode="RGBA", color=(0, 0, 0, 0)), 1568)
    assert image.media_type == "image/png" and decode(image).mode == "RGBA"


def test_transparent_flattened_on_white_when_png_too_big():
    # Noise under full transparency: a big PNG, but a plain white JPEG once flattened.
    noise = Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).convert("RGBA")
    noise.putalpha(0)
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")
    image = encode(buffer.getvalue(), 1568, 2000)
    assert image.media_type == "image/jpeg" and decode(image).getpixel((5, 5))[0] > 240


def test_unreadable_is_none():
    assert encode(b"not an image", 1568) is None




def test_shrinks_to_fit_budget():
    noise = Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3))
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")
    image = encode(buffer.getvalue(), 1568, 200_000)
    assert len(base64.b64decode(image.data)) <= 200_000 and decode(image).size[0] < 1200
    assert encode(buffer.getvalue(), 1568, 1000) is None


# Cited pages for follow-ups

async def test_pages_counts_across_images_and_pdf_pages():
    cover, pdf = discord_file("a_cover.png", data=png()), discord_file("b_ch.pdf", data=pdf_bytes(3))
    later = discord_file("c_extra.png", data=png())
    parts = await attachments.pages(upload(later, pdf, cover), 1568, [3, 1, 9])
    assert [p for p in parts if isinstance(p, str)] == [
        "Page 3 (b_ch.pdf, p. 2):", "Page 1 (a_cover.png):", "[Page 9: not in the upload or unreadable]"]
    assert all(isinstance(p, ImageData) for p in (parts[1], parts[3]))


async def test_pages_stop_downloading_after_last_wanted():
    first, rest = discord_file("p1.png", data=png()), discord_file("p2.pdf", data=pdf_bytes(2))
    await attachments.pages(upload(first, rest), 1568, [1])
    rest.read.assert_not_called()
