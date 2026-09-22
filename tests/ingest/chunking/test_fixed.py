"""The baseline: no structure, one window, sliding."""

from __future__ import annotations

from itertools import pairwise

import pytest

from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_every_chunk_is_a_verbatim_slice_of_content() -> None:
    document, context = await the_manual()

    for chunk in FixedWindowChunker().chunk(document, context):
        assert document.content[chunk.start : chunk.end] == chunk.text


@pytest.mark.asyncio
async def test_the_windows_cover_the_whole_document() -> None:
    """A baseline that dropped text would flatter itself on precision."""
    document, context = await the_manual()

    chunks = FixedWindowChunker().chunk(document, context)

    assert chunks[0].start == 0
    assert chunks[-1].end == len(document.content)
    for earlier, later in pairwise(chunks):
        assert later.start <= earlier.end, "a gap between windows loses text"


@pytest.mark.asyncio
async def test_consecutive_windows_overlap() -> None:
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=60, overlap_tokens=15).chunk(document, context)

    assert len(chunks) > 1
    for earlier, later in pairwise(chunks):
        assert later.start < earlier.end, "no overlap means a sentence can fall between windows"


@pytest.mark.asyncio
async def test_it_carries_no_section_path_and_no_prefix() -> None:
    """The baseline does not read headings. That difference is the measurement."""
    document, context = await the_manual()

    for chunk in FixedWindowChunker().chunk(document, context):
        assert chunk.section_path == ()
        assert chunk.context_prefix is None
        assert chunk.strategy is StrategyId.FIXED


@pytest.mark.asyncio
async def test_it_does_split_a_step_list() -> None:
    """Not a defect. Story 3.3 needs to attribute a retrieval failure to exactly
    this, so the baseline has to actually commit the error."""
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=40, overlap_tokens=8).chunk(document, context)
    holding = [chunk for chunk in chunks if "1. Verify the float shut-off" in chunk.text]

    assert holding, "the diagnostics list is in the document"
    assert "6. Check the battery state of charge" not in holding[0].text


@pytest.mark.asyncio
async def test_windows_do_not_cut_mid_word() -> None:
    """The one concession, so the baseline is fair rather than a straw man.

    A window that ends 'the squee' embeds a token sequence no query produces,
    and the comparison would be measuring tokenisation damage instead of
    boundary placement.
    """
    document, context = await the_manual()

    for chunk in FixedWindowChunker(target_tokens=50).chunk(document, context):
        if chunk.end < len(document.content):
            assert (
                document.content[chunk.end - 1].isspace() or document.content[chunk.end].isspace()
            )


@pytest.mark.asyncio
async def test_a_document_shorter_than_the_window_is_one_chunk() -> None:
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=100_000).chunk(document, context)

    assert len(chunks) == 1
    assert chunks[0].text == document.content
