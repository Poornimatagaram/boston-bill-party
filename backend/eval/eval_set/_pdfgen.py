"""Minimal, dependency-free text-bearing PDF writer for the Eval_Set.

The synthetic bills in the Eval_Set must be real PDFs that ``pdfplumber`` can
open AND from which it can extract the line-item text (a hand-crafted empty PDF
yields no text and would trip the Extractor's empty-text short-circuit, Req 2.7).
Rather than add a PDF-generation dependency to the pinned backend environment,
this module emits a small, valid single-font PDF using only the standard library.

Scope and limitations (deliberately tiny):

- One built-in Type1 font (Helvetica), left-aligned monospaced-ish layout.
- Only WinAnsi/ASCII-safe characters are emitted; the few non-ASCII glyphs used
  in bill text (e.g. a dollar sign) are plain ASCII, so no custom encoding is
  needed. Any non-latin1 character is replaced with ``?`` so the content stream
  stays valid.
- Multi-page output: lines beyond a page's capacity continue on a new page, and
  pages are emitted in order so ``pdfplumber`` concatenates them top-to-bottom
  (Req 2.1).

The produced text is what ``page.extract_text()`` returns, one input line per
visual line, which is exactly what the deterministic eval structuring path
(task 16.2) parses back into line items.
"""

from __future__ import annotations

# Page geometry (US Letter, 72 units/inch) and simple text layout constants.
_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN_LEFT = 54
_MARGIN_TOP = 738
_FONT_SIZE = 11
_LEADING = 15  # vertical distance between baselines
_LINES_PER_PAGE = (_MARGIN_TOP - 54) // _LEADING  # leave a bottom margin


def _escape_pdf_text(text: str) -> str:
    """Escape a string for a PDF literal string and drop non-latin1 glyphs."""
    out: list[str] = []
    for ch in text:
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        elif ch == "\t":
            out.append("    ")
        elif 32 <= ord(ch) <= 255:
            try:
                ch.encode("latin-1")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
        else:
            out.append("?")
    return "".join(out)


def _content_stream_for_page(lines: list[str]) -> bytes:
    """Build the content stream drawing ``lines`` top-to-bottom on one page."""
    parts: list[str] = ["BT", f"/F1 {_FONT_SIZE} Tf", f"{_LEADING} TL"]
    parts.append(f"1 0 0 1 {_MARGIN_LEFT} {_MARGIN_TOP} Tm")
    for index, line in enumerate(lines):
        escaped = _escape_pdf_text(line)
        if index == 0:
            parts.append(f"({escaped}) Tj")
        else:
            # T* advances to the next line using the set leading.
            parts.append("T*")
            parts.append(f"({escaped}) Tj")
    parts.append("ET")
    return ("\n".join(parts)).encode("latin-1")


def _paginate(lines: list[str]) -> list[list[str]]:
    """Split ``lines`` into page-sized chunks preserving order (Req 2.1)."""
    if not lines:
        return [[]]
    pages: list[list[str]] = []
    for start in range(0, len(lines), _LINES_PER_PAGE):
        pages.append(lines[start : start + _LINES_PER_PAGE])
    return pages


def build_pdf_bytes(lines: list[str]) -> bytes:
    """Return the bytes of a valid multi-page PDF rendering ``lines`` as text.

    The resulting PDF opens with ``pdfplumber`` and ``extract_text()`` returns
    the lines joined by newlines, in page then line order.
    """
    page_line_groups = _paginate(lines)

    objects: list[bytes] = []  # 1-indexed PDF objects, filled below

    # Object numbering plan:
    #   1: Catalog
    #   2: Pages (kids)
    #   3: Font (Helvetica)
    #   for each page i (0-based): page object and its content stream object
    n_pages = len(page_line_groups)
    first_page_obj = 4
    # Each page consumes two object numbers: the Page and its Contents stream.
    page_obj_numbers = [first_page_obj + 2 * i for i in range(n_pages)]
    content_obj_numbers = [first_page_obj + 2 * i + 1 for i in range(n_pages)]

    kids = " ".join(f"{num} 0 R" for num in page_obj_numbers)

    objects.append(
        b"<< /Type /Catalog /Pages 2 0 R >>"
    )  # obj 1
    objects.append(
        f"<< /Type /Pages /Count {n_pages} /Kids [{kids}] >>".encode("latin-1")
    )  # obj 2
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )  # obj 3

    # Page + content objects, interleaved to match the numbering plan.
    page_objects: list[bytes] = []
    for i, group in enumerate(page_line_groups):
        content = _content_stream_for_page(group)
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_obj_numbers[i]} 0 R >>"
        ).encode("latin-1")
        stream_obj = (
            f"<< /Length {len(content)} >>\nstream\n".encode("latin-1")
            + content
            + b"\nendstream"
        )
        page_objects.append(page_obj)
        page_objects.append(stream_obj)

    all_objects = objects + page_objects

    # Serialize with a cross-reference table.
    out = bytearray()
    out += b"%PDF-1.4\n"
    # Binary comment marker so tools treat the file as binary.
    out += b"%\xe2\xe3\xcf\xd3\n"

    offsets: list[int] = []
    for index, body in enumerate(all_objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1")
        out += body
        out += b"\nendobj\n"

    xref_offset = len(out)
    count = len(all_objects) + 1  # +1 for the free object 0
    out += f"xref\n0 {count}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += b"trailer\n"
    out += f"<< /Size {count} /Root 1 0 R >>\n".encode("latin-1")
    out += b"startxref\n"
    out += f"{xref_offset}\n".encode("latin-1")
    out += b"%%EOF\n"
    return bytes(out)
