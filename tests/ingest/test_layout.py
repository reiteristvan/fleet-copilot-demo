"""The Document Intelligence mapper, against real service output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.ingest.layout import layout_from_analyze_result
from fleet_copilot.ingest.parse import BlockRole, ParsedDocument, ParseError, ParserId

FIXTURES = Path(__file__).parent / "fixtures" / "layout"

SERVICE_MANUAL = "sdm-43-service-manual"
SCANNED_REPORT = "service-report-sd50b-2026-10142-17"
HUNGARIAN_DOCX = "sdr-90-operator-manual-hu"

STEMS = (SERVICE_MANUAL, SCANNED_REPORT, HUNGARIAN_DOCX)


def fixture(stem: str) -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return payload


def parsed(stem: str) -> ParsedDocument:
    return layout_from_analyze_result(fixture(stem), doc_id=stem, source_sha256="a" * 64)


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_is_a_verbatim_slice_of_content(stem: str) -> None:
    """The invariant the whole design rests on, checked against real output.

    ParsedDocument validates this itself, so a mapper that took text from
    paragraph.content rather than from the span fails here rather than produce
    chunks quoting something the document does not say.
    """
    document = parsed(stem)

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.parametrize("stem", STEMS)
def test_the_parser_and_model_are_recorded(stem: str) -> None:
    document = parsed(stem)

    assert document.parser is ParserId.AZURE_LAYOUT
    assert document.model_id == "prebuilt-layout"
    assert document.api_version == "2024-11-30"


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_carries_a_known_role(stem: str) -> None:
    """Page furniture is tagged, not dropped.

    Dropping it would shift every offset after it, and no span could then be
    checked against the cached JSON.
    """
    document = parsed(stem)

    assert document.blocks
    for block in document.blocks:
        assert block.role in set(BlockRole)


def test_the_service_manual_yields_headings() -> None:
    """Headings are what two of the three chunking strategies split on."""
    headings = parsed(SERVICE_MANUAL).headings()

    assert headings, "prebuilt-layout returned no headings for a document that has six"
    assert any("Safety" in block.text for block in headings)


def test_the_docx_yields_a_table_block() -> None:
    """ADR 0006 makes each table its own chunk, so the mapper must emit one.

    Only the DOCX path produces a table: corpus/formats.py renders a table into
    a PDF as space-separated text with no ruling, so there is nothing for the
    service to recognise. tests/ingest/test_fixtures.py pins that asymmetry.
    """
    tables = parsed(HUNGARIAN_DOCX).tables()

    assert tables, "the DOCX table did not survive mapping"
    assert "1.534-210.0" in tables[0].text, "the first row's item number"


def test_the_service_serialises_a_table_as_html_not_markdown() -> None:
    """Recorded because ADR 0006 assumes Markdown, and this is not that.

    In Markdown output mode the service still emits tables as HTML -- <table>,
    <tr>, <th> -- while the Markdown parser emits pipe rows straight from the
    source. So the two parsers disagree on exactly one thing, and it is the one
    ADR 0006 singles out for its own chunk type.

    Plan 2 has to reconcile them: either the table chunker normalises both to
    one form, or a chunk's serialisation depends on which parser ran and the
    strategy comparison quietly stops being like-for-like.
    """
    table = parsed(HUNGARIAN_DOCX).tables()[0]

    assert table.text.startswith("<table>")
    assert "|" not in table.text


def test_table_cells_are_not_also_emitted_as_paragraphs() -> None:
    """The service reports a table's cells as paragraphs as well as in the table.

    Emitting both would put every row in the index twice, once inside the table
    chunk and once as loose prose. ParsedDocument rejects overlapping blocks, so
    this passing means the mapper dropped the duplicates.
    """
    document = parsed(HUNGARIAN_DOCX)
    table = document.tables()[0]

    inside = [
        block
        for block in document.blocks
        if block.role is not BlockRole.TABLE
        and block.start >= table.start
        and block.end <= table.end
    ]
    assert inside == []


def test_the_scanned_report_yields_blocks() -> None:
    """An image-only PDF. Any block at all came out of OCR."""
    assert parsed(SCANNED_REPORT).blocks


def test_an_empty_content_payload_is_refused() -> None:
    """A successful call that returned nothing is the F0 failure mode.

    It must not become an empty ParsedDocument that gets cached and then read
    forever by every chunker as a document with nothing in it.
    """
    with pytest.raises(ParseError, match="no content"):
        layout_from_analyze_result(
            {"apiVersion": "2024-11-30", "modelId": "prebuilt-layout", "content": ""},
            doc_id="empty",
            source_sha256="a" * 64,
        )
