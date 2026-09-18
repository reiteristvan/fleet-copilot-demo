"""The offline parser: what it can do, and what it must refuse to fake."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_copilot.ingest.fallback import LocalParser
from fleet_copilot.ingest.parse import DocumentParser, ParsedDocument, ParseError, ParserId

PUBLISHED = Path(__file__).resolve().parents[2] / "data" / "corpus" / "published"

TEXT_PDF = "sdm-43-service-manual.pdf"
SCANNED_PDF = "service-report-sd50b-2026-10142-17.pdf"
DOCX = "sdr-90-operator-manual-hu.docx"

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.asyncio
async def test_a_pdf_with_a_text_layer_is_parsed() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    assert isinstance(document, ParsedDocument)
    assert document.parser is ParserId.LOCAL
    assert "squeegee" in document.content.lower()


@pytest.mark.asyncio
async def test_every_block_is_a_verbatim_slice_of_content() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.asyncio
async def test_a_scanned_pdf_raises_instead_of_returning_nothing() -> None:
    """The whole reason the Azure path is worth paying for.

    Five of the twenty PDFs in this corpus have no text layer, and one hides a
    planted prompt injection. A fallback that returned an empty document for them
    would keep the suite green while the OCR path went untested.
    """
    with pytest.raises(ParseError, match="no text layer"):
        await LocalParser().parse(
            (PUBLISHED / SCANNED_PDF).read_bytes(),
            doc_id="service-report-sd50b-2026-10142-17",
            content_type=PDF_TYPE,
        )


@pytest.mark.asyncio
async def test_a_docx_is_parsed() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / DOCX).read_bytes(), doc_id="sdr-90-operator-manual-hu", content_type=DOCX_TYPE
    )

    assert document.content.strip()


@pytest.mark.asyncio
async def test_it_produces_no_headings_and_no_tables() -> None:
    """Not a silent limitation. A chunking comparison run against this parser
    would measure the fallback rather than the pipeline, and this says so."""
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    assert document.headings() == ()
    assert document.tables() == ()


@pytest.mark.asyncio
async def test_an_unsupported_content_type_raises() -> None:
    with pytest.raises(ParseError, match="cannot parse"):
        await LocalParser().parse(b"whatever", doc_id="x", content_type="image/png")


def test_local_parser_satisfies_the_parser_protocol() -> None:
    """mypy checks this statically; this catches signature drift at runtime."""
    assert isinstance(LocalParser(), DocumentParser)
