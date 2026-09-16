"""Write a planned document as PDF, scanned PDF or DOCX, reproducibly.

Every writer here is byte-deterministic, which took one fix each and is the
property the committed manifest depends on:

- **PDF** stamps the current time into ``CreationDate`` unless it is pinned, so
  :func:`_new_pdf` pins it and the producer string.
- **DOCX** writes the current time into every zip entry header, and the header
  also records which platform wrote it. The member *contents* are already
  deterministic -- python-docx's template carries a fixed ``dcterms:created`` --
  so the fix is entirely in the entry headers: pin the timestamp and pin the
  create-system byte, which ``zipfile`` otherwise takes from ``sys.platform``.
- **Scanned PDF** is drawn with Pillow but assembled with fpdf2, because
  Pillow's own PDF writer stamps an unpinnable creation date. Going through one
  writer means one place where reproducibility can break.

A converted document replaces its Markdown rather than accompanying it
(ADR 0003), so these formats carry the front-matter facts as a visible
information block. Without it a PDF manual would reach the pipeline with no
revision, no effective date and no item number -- and for the scanned subset
that block is only readable through OCR, which is the point of having it there.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Final

from docx import Document as DocxDocument
from docx.shared import Pt
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from fleet_copilot.corpus.document import (
    Block,
    Bullets,
    DocumentPlan,
    FrontMatter,
    Paragraph,
    Steps,
    Table,
)

PINNED_TIMESTAMP: Final = datetime(2026, 1, 1, tzinfo=UTC)
"""The creation date every generated PDF claims.

An arbitrary fixed instant. What matters is that it does not move between runs.
"""

PRODUCER: Final = "fleet-copilot-corpus"
"""Producer and creator string, pinned so a library upgrade does not rewrite
every hash in the manifest.
"""

_ZIP_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
"""The earliest timestamp the zip format can represent."""

_ZIP_CREATE_SYSTEM: Final = 0
"""The "created by" byte written into every zip entry header.

``zipfile.ZipInfo`` defaults this from ``sys.platform`` -- 0 for Windows, 3 for
Unix -- so a DOCX built on a laptop and the same DOCX built in CI differ by one
byte per entry and nothing else. Pinning it to 0 (MS-DOS/FAT) is what makes the
committed hash mean the same thing on both. Word does not read this field.
"""

_PAGE_WIDTH_MM: Final = 210
_PAGE_HEIGHT_MM: Final = 297
_MARGIN_MM: Final = 18

_SCAN_WIDTH_PX: Final = 1240
_SCAN_HEIGHT_PX: Final = 1754
_SCAN_SKEW_DEGREES: Final = 0.45
_SCAN_PAPER = 246
_SCAN_INK = 30


def _info_lines(front: FrontMatter) -> list[str]:
    """The front-matter facts, as the lines a real document would print."""
    lines = [
        f"Document {front.doc_id} · revision {front.revision} · "
        f"effective {front.effective_date.isoformat()}",
    ]
    if front.machine_types:
        lines.append("Machine types: " + ", ".join(front.machine_types))
    if front.item_numbers:
        lines.append("Item numbers: " + ", ".join(front.item_numbers))
    if front.serials:
        lines.append("Serial numbers: " + ", ".join(front.serials))
    if front.site:
        lines.append(f"Site: {front.site}")
    return lines


def _plain_lines(plan: DocumentPlan) -> list[str]:
    """Flatten ``plan`` into the lines a text renderer lays out in order."""
    lines = [plan.title, ""]
    lines.extend(_info_lines(plan.front_matter))
    for section in plan.sections:
        lines.extend(["", section.heading, ""])
        lines.extend(_block_lines(section.blocks))
    return lines


def _block_lines(blocks: tuple[Block, ...]) -> list[str]:
    """Render a run of blocks as flat text lines."""
    lines: list[str] = []
    for block in blocks:
        match block:
            case Paragraph():
                lines.extend([block.text, ""])
            case Bullets():
                lines.extend([f"- {item}" for item in block.items])
                lines.append("")
            case Steps():
                lines.extend(
                    f"{number}. {item}" for number, item in enumerate(block.items, start=1)
                )
                lines.append("")
            case Table():
                lines.append("  ".join(block.header))
                lines.extend("  ".join(row) for row in block.rows)
                lines.append("")
    return lines


_LATIN1_TYPOGRAPHY: Final = {
    "—": "-",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
}
"""Typographic characters mapped to ASCII for the PDF writers.

fpdf2's core fonts are Latin-1, which has no em dash. Embedding a Unicode TTF
to gain one would add a font licence to the repository; mapping the handful of
characters involved costs nothing and changes no meaning.
"""


def _latin1(text: str) -> str:
    """Make ``text`` renderable by a Latin-1 core font, or refuse.

    Known typography is transliterated. Anything else outside Latin-1 raises,
    naming the character: the case that would otherwise reach here is a
    Hungarian document routed to a PDF, and silently mangling its accents into
    a corpus used to measure cross-language retrieval is worse than failing.
    """
    for source, replacement in _LATIN1_TYPOGRAPHY.items():
        text = text.replace(source, replacement)
    try:
        text.encode("latin-1")
    except UnicodeEncodeError as error:
        bad = text[error.start : error.end]
        msg = f"character {bad!r} cannot be written to a Latin-1 core-font PDF: {text!r}"
        raise ValueError(msg) from error
    return text


def _new_pdf(plan: DocumentPlan) -> FPDF:
    """Return an FPDF with its date and producer pinned for reproducibility."""
    pdf = FPDF(unit="mm", format=(_PAGE_WIDTH_MM, _PAGE_HEIGHT_MM))
    pdf.set_creation_date(PINNED_TIMESTAMP)
    pdf.set_producer(PRODUCER)
    pdf.set_creator(PRODUCER)
    pdf.set_title(_latin1(plan.title))
    pdf.set_subject(plan.front_matter.type.value)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN_MM)
    pdf.set_margins(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM)
    return pdf


def render_pdf(plan: DocumentPlan) -> bytes:
    """Render ``plan`` as a PDF with a real text layer."""
    pdf = _new_pdf(plan)
    pdf.add_page()
    width = _PAGE_WIDTH_MM - 2 * _MARGIN_MM

    pdf.set_font("Helvetica", style="B", size=16)
    pdf.multi_cell(width, 8, _latin1(plan.title))
    pdf.ln(2)

    pdf.set_font("Helvetica", size=8)
    for line in _info_lines(plan.front_matter):
        pdf.multi_cell(width, 4, _latin1(line))
    pdf.ln(4)

    for section in plan.sections:
        pdf.set_font("Helvetica", style="B", size=12)
        pdf.multi_cell(width, 6, _latin1(section.heading))
        pdf.ln(1)
        pdf.set_font("Helvetica", size=10)
        for line in _block_lines(section.blocks):
            if line:
                pdf.multi_cell(width, 5, _latin1(line))
            else:
                pdf.ln(3)
    return bytes(pdf.output())


def _scan_font(size: int) -> ImageFont.FreeTypeFont:
    """Return Pillow's bundled scalable default font at ``size``.

    ``load_default`` returns a FreeType-backed font when given a size and a
    small fixed bitmap font otherwise. The check keeps that explicit: silently
    falling back to the bitmap font would render every scanned page at a size
    OCR cannot read, and the pages would still look plausible in a thumbnail.
    """
    font = ImageFont.load_default(size=size)
    if not isinstance(font, ImageFont.FreeTypeFont):
        msg = "Pillow returned a bitmap default font; a scalable font is required"
        raise RuntimeError(msg)
    return font


def _wrap(text: str, font: ImageFont.FreeTypeFont, width_px: int) -> list[str]:
    """Greedily wrap ``text`` to ``width_px`` using ``font``'s real metrics."""
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.getlength(candidate) > width_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def render_scanned_pdf(plan: DocumentPlan) -> bytes:
    """Render ``plan`` as an image-only PDF, with no text layer at all.

    The result carries no font and no text-showing operator, so the words are
    recoverable only by OCR. That is what makes the injection planted in one of
    these a test of the OCR path rather than a test of the PDF parser.
    """
    font = _scan_font(22)
    heading_font = _scan_font(30)
    margin = 90
    text_width = _SCAN_WIDTH_PX - 2 * margin

    page = Image.new("L", (_SCAN_WIDTH_PX, _SCAN_HEIGHT_PX), color=_SCAN_PAPER)
    draw = ImageDraw.Draw(page)
    y = margin

    for index, line in enumerate(_plain_lines(plan)):
        chosen = heading_font if index == 0 else font
        for wrapped in _wrap(line, chosen, text_width):
            if y > _SCAN_HEIGHT_PX - margin:
                break
            draw.text((margin, y), wrapped, font=chosen, fill=_SCAN_INK)
            y += int(chosen.size * 1.45)
        y += 4

    # A page that went through a document scanner is never perfectly square.
    page = page.rotate(_SCAN_SKEW_DEGREES, resample=Image.Resampling.BICUBIC, fillcolor=_SCAN_PAPER)

    jpeg = io.BytesIO()
    page.save(jpeg, format="JPEG", quality=72, optimize=False)
    jpeg.seek(0)

    pdf = _new_pdf(plan)
    pdf.add_page()
    pdf.image(jpeg, x=0, y=0, w=_PAGE_WIDTH_MM, h=_PAGE_HEIGHT_MM)
    return bytes(pdf.output())


def _normalise_zip(data: bytes) -> bytes:
    """Rebuild a zip with pinned entry timestamps, preserving order and method.

    python-docx writes the current time into each entry header, so two
    otherwise-identical documents saved seconds apart differ in bytes while
    every member's *contents* are identical. Pinning the timestamps is what
    makes a DOCX hash stable enough to commit.
    """
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as destination:
        for info in source.infolist():
            pinned = zipfile.ZipInfo(info.filename, date_time=_ZIP_TIMESTAMP)
            pinned.compress_type = info.compress_type
            pinned.external_attr = info.external_attr
            pinned.create_system = _ZIP_CREATE_SYSTEM
            destination.writestr(pinned, source.read(info.filename))
    return output.getvalue()


def render_docx(plan: DocumentPlan) -> bytes:
    """Render ``plan`` as a Word document."""
    document = DocxDocument()
    document.add_heading(plan.title, level=1)

    for line in _info_lines(plan.front_matter):
        paragraph = document.add_paragraph()
        run = paragraph.add_run(line)
        run.font.size = Pt(8)

    for section in plan.sections:
        document.add_heading(section.heading, level=2)
        for block in section.blocks:
            match block:
                case Paragraph():
                    document.add_paragraph(block.text)
                case Bullets():
                    for item in block.items:
                        document.add_paragraph(item, style="List Bullet")
                case Steps():
                    for item in block.items:
                        document.add_paragraph(item, style="List Number")
                case Table():
                    table = document.add_table(rows=1, cols=len(block.header))
                    table.style = "Table Grid"
                    for cell, text in zip(table.rows[0].cells, block.header, strict=True):
                        cell.text = text
                    for row in block.rows:
                        cells = table.add_row().cells
                        for cell, text in zip(cells, row, strict=True):
                            cell.text = text

    buffer = io.BytesIO()
    document.save(buffer)
    return _normalise_zip(buffer.getvalue())
