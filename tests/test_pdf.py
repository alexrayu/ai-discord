from datetime import date

import pdf


def test_renders_pdf():
    data = pdf.render("Review: p1", "## Verdict\n\n- **Weak** ending, p. 3\n\n影の中で", date(2026, 10, 14))
    assert data.startswith(b"%PDF") and len(data) > 1000


def test_raw_html_escaped_and_images_refused(monkeypatch):
    calls = []
    original = pdf.OFFLINE.fetch

    def spy(url, *args, **kwargs):
        calls.append(url)
        return original(url, *args, **kwargs)

    monkeypatch.setattr(pdf.OFFLINE, "fetch", spy)
    data = pdf.render("t", '<img src="http://example.invalid/a.png"> ![x](http://example.invalid/b.png)')
    assert data.startswith(b"%PDF")
    # The raw tag is escaped and never requested; the Markdown image is requested and refused without network.
    assert calls == ["http://example.invalid/b.png"]


def test_nested_items_indented_for_markdown():
    assert pdf.NESTED_ITEM.sub("    ", "1. a\n   - b\n  - c\n- d\n    - e") == "1. a\n    - b\n    - c\n- d\n    - e"
