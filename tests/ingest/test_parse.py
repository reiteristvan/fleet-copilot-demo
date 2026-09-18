"""The contract every parser produces and every chunker consumes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParserId,
)

CONTENT = "# Manual\n\nBody text here.\n"


def a_document(**overrides: object) -> ParsedDocument:
    """Build a valid ParsedDocument, with fields replaced for the case at hand."""
    fields: dict[str, object] = {
        "doc_id": "manual",
        "source_sha256": "0" * 64,
        "parser": ParserId.MARKDOWN,
        "model_id": "markdown",
        "api_version": None,
        "content": CONTENT,
        "blocks": (
            ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
            ParsedBlock(
                role=BlockRole.PARAGRAPH, text="Body text here.", start=10, end=25, page_number=1
            ),
        ),
        "pages": (ParsedPage(page_number=1, start=0, end=len(CONTENT)),),
        "parsed_at": datetime(2026, 9, 17, tzinfo=UTC),
    }
    fields.update(overrides)
    return ParsedDocument.model_validate(fields)


def test_a_valid_document_round_trips() -> None:
    document = a_document()
    first = document.blocks[0]

    assert document.content[first.start : first.end] == "# Manual"


def test_a_block_whose_span_does_not_match_its_text_is_rejected() -> None:
    """Every chunker slices content by offset; a lying span mis-slices silently."""
    with pytest.raises(ValidationError, match="does not match"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH, text="Body text here.", start=0, end=15, page_number=1
                ),
            )
        )


def test_blocks_must_be_in_reading_order() -> None:
    """Chunk indices come from block order, and an index ordered wrongly is wrong."""
    with pytest.raises(ValidationError, match="reading order"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH,
                    text="Body text here.",
                    start=10,
                    end=25,
                    page_number=1,
                ),
                ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
            )
        )


def test_blocks_must_not_overlap() -> None:
    """A table's cells arrive as paragraphs as well as in the table.

    Emitting both would double every table's text: once inside a TABLE chunk and
    once as loose prose, so a retriever would see each row twice and a citation
    could land on either copy.
    """
    with pytest.raises(ValidationError, match="overlaps"):
        a_document(
            blocks=(
                ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
                ParsedBlock(role=BlockRole.PARAGRAPH, text="Manual", start=2, end=8, page_number=1),
            )
        )


def test_a_block_reaching_past_the_content_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reaches past"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH, text="x" * 99, start=0, end=99, page_number=1
                ),
            )
        )


def test_headings_returns_titles_and_section_headings_only() -> None:
    assert tuple(block.role for block in a_document().headings()) == (BlockRole.TITLE,)


def test_tables_returns_table_blocks_only() -> None:
    assert a_document().tables() == ()


def test_a_naive_parsed_at_is_rejected() -> None:
    """Parses run on more than one machine; a naive timestamp cannot be ordered."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        a_document(parsed_at=datetime(2026, 9, 17))  # noqa: DTZ001 - the point of the test
