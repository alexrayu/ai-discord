"""Review material from an upload message: page images, PDFs and scripts. Bytes stay in memory, never on disk."""

import asyncio
import base64
import io
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import PurePath

import discord
import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from models import ImageData, Part, PdfData

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".php", ".js", ".ts", ".json", ".yml", ".yaml", ".csv", ".html", ".css", ".twig", ".sql", ".log",
}
MB = 1024 * 1024
MAX_BYTES = {"image": 50 * MB, "pdf": 100 * MB, "text": 1 * MB}
TEXT_MAX_CHARS = 100_000
# PDFs go to the providers as is (text layer kept) up to this total; the rest are rendered to page images.
NATIVE_PDF_MAX_BYTES = 20 * MB
# Raw bytes of all images and PDFs per request: base64 (+1/3) stays under Anthropic's 32 MB request limit.
REQUEST_BUDGET = 22 * MB
MAX_IMAGES = 100  # per request
MAX_ENCODED = 3_750_000  # base64 of this stays under the 5 MB per-image API limit
MIN_EDGE = 640  # images are shrunk down to this to fit the budget, then given up
# PDFium is not thread-safe, not even across documents.
PDFIUM = threading.Lock()


def kind_of(filename: str, media_type: str) -> str:
    """By content type, with the file extension as fallback."""
    extension = PurePath(filename).suffix.lower()
    if media_type.startswith("image/") or extension in IMAGE_EXTENSIONS:
        return "image"
    if media_type == "application/pdf" or extension == ".pdf":
        return "pdf"
    if media_type.startswith("text/") or extension in TEXT_EXTENSIONS:
        return "text"
    return "other"


def _kind(att: discord.Attachment) -> str:
    return kind_of(att.filename, (att.content_type or "").split(";")[0].strip().lower())


def check(message: discord.Message) -> tuple[bool, list[str]]:
    """(any usable file, reasons for the oversized ones). Files of other kinds are ignored."""
    usable, oversized = False, []
    for att in message.attachments:
        if (kind := _kind(att)) not in MAX_BYTES:
            continue
        if att.size <= MAX_BYTES[kind]:
            usable = True
        else:
            oversized.append(f"{_name(att.filename)} is {att.size / MB:.1f} MB, max {MAX_BYTES[kind] // MB} MB")
    return usable, oversized


def natural_key(filename: str) -> list[str | int]:
    """page2 before page10. Digit runs land on odd indexes, so ints are only compared with ints."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", filename)]


def _name(filename: str) -> str:
    return re.sub(r"[\[\]\r\n]", "", filename)


def has_content(material: list[Part]) -> bool:
    """False when every file became a note, so a review would see nothing."""
    return any(not isinstance(p, str) or p.startswith("--- file:") for p in material)


@dataclass
class _File:
    name: str
    kind: str
    raw: bytes | None
    native: bool = False  # PDF passed as is
    pages: int = 0  # images this file contributes: 1 per image, rendered pages for a PDF that isn't native


async def collect(message: discord.Message, max_edge: int) -> list[Part]:
    """Material in filename order. Image pages are numbered 1..N across files; unreadable files become notes.

    Two passes: downloads and page counts first, so every image gets an equal share of the request budget.
    """
    files = []
    for att in sorted(message.attachments, key=lambda a: natural_key(a.filename)):
        if (kind := _kind(att)) in MAX_BYTES:
            files.append(_File(_name(att.filename), kind, await _read(att, MAX_BYTES[kind])))
    images = native = 0
    for f in files:
        if f.raw is None or f.kind == "text":
            continue
        count = 1 if f.kind == "image" else await asyncio.to_thread(page_count, f.raw)
        # Claude takes at most MAX_IMAGES PDF pages; longer or unreadable PDFs go the rendering route.
        if f.kind == "pdf" and 0 < count <= MAX_IMAGES and native + len(f.raw) <= NATIVE_PDF_MAX_BYTES:
            f.native = True
            native += len(f.raw)
            continue
        f.pages = min(count, MAX_IMAGES - images)
        images += f.pages
    per_image = min(MAX_ENCODED, (REQUEST_BUDGET - native) // max(images, 1))

    parts: list[Part] = []
    page = 0
    for f in files:
        if f.raw is None:
            parts.append(f"[{f.name}: too large or unavailable]")
        elif f.kind == "text":
            text = f.raw.decode("utf-8", errors="replace")
            if len(text) > TEXT_MAX_CHARS:
                text = text[:TEXT_MAX_CHARS] + "\n[truncated]"
            parts.append(f"--- file: {f.name} ---\n{text}")
        elif f.native:
            parts += [f"PDF: {f.name}", PdfData(f.name, base64.b64encode(f.raw).decode())]
        elif f.kind == "pdf" and not f.pages:
            parts.append(f"[{f.name}: unreadable PDF or over the {MAX_IMAGES}-page limit]")
        elif f.kind == "pdf":
            rendered, total = await asyncio.to_thread(render_pdf, f.raw, range(f.pages), max_edge, per_image)
            for i, image in enumerate(rendered, 1):
                page += 1
                label = f"Page {page} ({f.name}, p. {i})"
                parts += [f"{label}:", image] if image else [f"[{label}: unreadable page]"]
            if total > f.pages:
                parts.append(f"[{f.name}: pages {f.pages + 1}-{total} left out, {MAX_IMAGES} pages per review]")
        elif f.pages:
            page += 1
            image = await asyncio.to_thread(encode, f.raw, max_edge, per_image)
            parts += [f"Page {page} ({f.name}):", image] if image else [f"[Page {page} ({f.name}): unreadable image]"]
        else:
            parts.append(f"[{f.name}: left out, {MAX_IMAGES} pages per review]")
    return parts


async def pages(message: discord.Message, max_edge: int, wanted: list[int]) -> list[Part]:
    """Only the wanted pages, numbered like a review counts them: images and every PDF page in filename order.

    Downloads stop once the highest wanted page is reached.
    """
    found: dict[int, tuple[str, ImageData | None]] = {}
    page, last = 0, max(wanted)
    for att in sorted(message.attachments, key=lambda a: natural_key(a.filename)):
        if page >= last:
            break
        kind, name = _kind(att), _name(att.filename)
        if kind == "image":
            page += 1
            if page in wanted:
                found[page] = (name, await _image(att, max_edge))
        elif kind == "pdf" and (raw := await _read(att, MAX_BYTES["pdf"])):
            count = await asyncio.to_thread(page_count, raw)
            indexes = [n - page - 1 for n in wanted if page < n <= page + count]
            if indexes:
                rendered, _ = await asyncio.to_thread(render_pdf, raw, indexes, max_edge, MAX_ENCODED)
                found |= {page + 1 + i: (f"{name}, p. {i + 1}", image) for i, image in zip(indexes, rendered)}
            page += count
    parts: list[Part] = []
    for n in wanted:
        name, image = found.get(n, (None, None))
        parts += [f"Page {n} ({name}):", image] if image else [f"[Page {n}: not in the upload or unreadable]"]
    return parts


async def _image(att: discord.Attachment, max_edge: int) -> ImageData | None:
    raw = await _read(att, MAX_BYTES["image"])
    return await asyncio.to_thread(encode, raw, max_edge) if raw else None


async def _read(att: discord.Attachment, max_bytes: int) -> bytes | None:
    if att.size > max_bytes:
        return None
    try:
        return await att.read()
    except discord.HTTPException as e:
        log.warning("Attachment unavailable: %s %s", e.status, e.code)
        return None


def page_count(raw: bytes) -> int:
    """0 for an unreadable or encrypted PDF."""
    with PDFIUM:
        try:
            pdf = pdfium.PdfDocument(raw)
        except pdfium.PdfiumError:
            return 0
        try:
            return len(pdf)
        finally:
            pdf.close()


def render_pdf(raw: bytes, indexes, max_edge: int, max_encoded: int) -> tuple[list[ImageData | None], int]:
    """The pages at the given 0-based indexes as encoded images, and the PDF's total page count."""
    with PDFIUM:
        pdf = pdfium.PdfDocument(raw)
        try:
            images = []
            for i in indexes:
                page = pdf[i]
                try:
                    # Points are 1/72 inch; render straight at the target size instead of print resolution.
                    img = page.render(scale=max_edge / max(page.get_size())).to_pil()
                finally:
                    page.close()
                images.append(_encode(img, max_edge, max_encoded))
            return images, len(pdf)
        finally:
            pdf.close()


def encode(raw: bytes, max_edge: int, max_encoded: int = MAX_ENCODED) -> ImageData | None:
    """Downscale to max_edge; PNG when transparent, else JPEG; None if unreadable."""
    try:
        with Image.open(io.BytesIO(raw)) as source:
            return _encode(ImageOps.exif_transpose(source), max_edge, max_encoded)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return None


def _encode(img: Image.Image, max_edge: int, max_encoded: int) -> ImageData | None:
    """Within max_encoded bytes: lower JPEG quality first, then smaller sizes down to MIN_EDGE."""
    img.thumbnail((max_edge, max_edge))
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        if len(png := _save(rgba, "PNG")) <= max_encoded:
            return ImageData("image/png", base64.b64encode(png).decode())
        # Flatten on white: dropping alpha alone turns transparent areas black.
        img = Image.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.getchannel("A"))
    rgb = img.convert("RGB")
    while True:
        for quality in (90, 75, 60):
            if len(jpeg := _save(rgb, "JPEG", quality=quality)) <= max_encoded:
                return ImageData("image/jpeg", base64.b64encode(jpeg).decode())
        if max(rgb.size) <= MIN_EDGE:
            return None
        rgb = rgb.resize((max(1, rgb.width * 3 // 4), max(1, rgb.height * 3 // 4)), Image.LANCZOS)


def _save(img: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, fmt, optimize=True, **options)
    return buffer.getvalue()
