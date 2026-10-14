"""Final review as a downloadable PDF: Markdown -> HTML -> PDF, offline."""

import html
import re
from datetime import date

import markdown
from weasyprint import HTML
from weasyprint.urls import URLFetcher

CSS = """
@page { size: A4; margin: 2cm 2.2cm; @bottom-center { content: counter(page); font-size: 9pt; color: #777; } }
body { font-family: "Noto Serif", "Noto Serif CJK JP", serif; font-size: 10.5pt; line-height: 1.5; color: #111; }
header { border-bottom: 1px solid #999; margin-bottom: 1.2em; }
header h1 { font-size: 18pt; margin: 0; }
header p { color: #555; margin: .3em 0 .8em; }
h1, h2, h3 { font-family: "Noto Sans", "Noto Sans CJK JP", sans-serif; break-after: avoid; }
h2 { font-size: 13pt; margin-top: 1.4em; }
h3 { font-size: 11pt; }
li { margin: .2em 0; }
code { font-size: 9.5pt; }
blockquote { margin-left: 0; padding-left: 1em; border-left: 3px solid #ccc; color: #444; }
"""
# Models indent sub-items by 2 or 3 spaces; Python-Markdown nests only at 4.
NESTED_ITEM = re.compile(r"^ {1,3}(?=(?:[-*+]|\d+\.) )", re.MULTILINE)
# Model output is untrusted: no remote or local resources, whatever the Markdown asks for.
OFFLINE = URLFetcher(allowed_protocols=())


def render(title: str, text: str, day: date | None = None) -> bytes:
    """Markdown review to PDF bytes. Raw HTML in the text is shown as text, not rendered."""
    escaped = NESTED_ITEM.sub("    ", text.replace("&", "&amp;").replace("<", "&lt;"))
    body = markdown.markdown(escaped, extensions=["sane_lists"])
    day = day or date.today()
    page = (
        f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
        f"<header><h1>{html.escape(title)}</h1><p>Editorial review by Claude and Quill, {day.isoformat()}</p></header>"
        f"{body}</body></html>"
    )
    return HTML(string=page, url_fetcher=OFFLINE).write_pdf()
