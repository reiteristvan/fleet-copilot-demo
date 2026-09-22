"""Heading-aware chunking, and the two things it refuses to break."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_copilot.corpus.build import manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.ingest.chunking.context import DocumentContext
from fleet_copilot.ingest.chunking.structural import StructuralChunker, is_step_line
from fleet_copilot.ingest.markdown import MarkdownParser
from fleet_copilot.ingest.models import StrategyId
from fleet_copilot.ingest.parse import ParsedDocument

CORPUS = Path(__file__).resolve().parents[3] / "data" / "corpus" / "markdown"
MANUAL = CORPUS / "sdm-43-service-manual.md"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"


async def the_manual() -> tuple[ParsedDocument, DocumentContext]:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )
    manifest = load_manifest(manifest_path(None))
    entry = next(e for e in manifest.documents if e.doc_id == "sdm-43-service-manual")
    return document, DocumentContext.from_entry(entry)


def test_step_lines_are_recognised_and_bullets_are_not() -> None:
    """A procedure is a sequence; half of one reads as a whole one and is
    dangerous. A bullet list is a set of independent statements, so splitting it
    costs nothing but a little context."""
    assert is_step_line("1. Verify the float shut-off operates freely.")
    assert is_step_line("  10. Measure at the connector.")
    assert not is_step_line("- Order consumables against the item number.")
    assert not is_step_line("Ordinary prose.")


@pytest.mark.asyncio
async def test_every_chunk_is_a_verbatim_slice_of_content() -> None:
    document, context = await the_manual()

    for chunk in StructuralChunker().chunk(document, context):
        assert document.content[chunk.start : chunk.end] == chunk.text


@pytest.mark.asyncio
async def test_chunk_indices_are_contiguous_and_start_at_zero() -> None:
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.strategy is StrategyId.STRUCTURAL for chunk in chunks)


@pytest.mark.asyncio
async def test_a_table_is_its_own_chunk_and_holds_its_header_row() -> None:
    """ADR 0006. An interval table answers a different question from the
    paragraph above it, and a row separated from its column headers is noise."""
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)
    table_chunks = [chunk for chunk in chunks if chunk.text.lstrip().startswith("|")]

    assert len(table_chunks) == 1
    table = table_chunks[0]
    assert "| Interval | Task |" in table.text
    assert "1000 h" in table.text
    assert "For service technicians" not in table.text


@pytest.mark.asyncio
async def test_a_step_list_is_never_split() -> None:
    """The diagnostics list has six steps. They stay in one chunk even when that
    chunk overruns the target, because half a procedure reads as a whole one."""
    document, context = await the_manual()

    chunks = StructuralChunker(target_tokens=40).chunk(document, context)

    holding = [chunk for chunk in chunks if "1. Verify the float shut-off" in chunk.text]
    assert len(holding) == 1
    assert "6. Check the battery state of charge" in holding[0].text


@pytest.mark.asyncio
async def test_a_chunk_carries_the_section_it_came_from() -> None:
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)

    safety = next(chunk for chunk in chunks if "emergency stop is not an isolator" in chunk.text)
    assert safety.section_path[-1] == "Safety"
    assert safety.machine_types == ("SDM-43",)
    assert safety.context_prefix is None, "the header belongs to the contextual strategy"


@pytest.mark.asyncio
async def test_a_document_always_produces_at_least_one_chunk() -> None:
    document, context = await the_manual()

    assert StructuralChunker().chunk(document, context)
