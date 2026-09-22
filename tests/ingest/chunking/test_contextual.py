"""Strategy 3 differs from strategy 2 by one field, and this proves it."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.chunking.structural import ContextualChunker, StructuralChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_the_boundaries_are_identical_to_the_structural_ones() -> None:
    """The whole point of the A/B.

    If the two strategies cut in different places, story 3.3 cannot tell whether
    a difference came from the header or from the boundaries, and the experiment
    has two variables.
    """
    document, context = await the_manual()

    structural = StructuralChunker().chunk(document, context)
    contextual = ContextualChunker().chunk(document, context)

    assert [(c.start, c.end) for c in structural] == [(c.start, c.end) for c in contextual]
    assert [c.text for c in structural] == [c.text for c in contextual]


@pytest.mark.asyncio
async def test_every_chunk_carries_a_header() -> None:
    document, context = await the_manual()

    for chunk in ContextualChunker().chunk(document, context):
        assert chunk.context_prefix
        assert chunk.strategy is StrategyId.CONTEXTUAL


@pytest.mark.asyncio
async def test_the_header_puts_the_machine_type_in_reach_of_a_query() -> None:
    """The reason strategy 3 exists: the safety bullet never names the machine."""
    document, context = await the_manual()

    safety = next(
        chunk
        for chunk in ContextualChunker().chunk(document, context)
        if "emergency stop is not an isolator" in chunk.text
    )

    assert "SDM-43" not in safety.text
    assert "SDM-43" in safety.embed_text
    assert "Safety" in safety.embed_text


@pytest.mark.asyncio
async def test_the_content_hashes_differ_from_the_structural_ones() -> None:
    """ADR 0006's collision, checked end to end.

    Same slice, different header, so the embedding cache must see two keys. One
    key would score this strategy on the other's vectors.
    """
    document, context = await the_manual()

    structural = {c.content_hash for c in StructuralChunker().chunk(document, context)}
    contextual = {c.content_hash for c in ContextualChunker().chunk(document, context)}

    assert structural.isdisjoint(contextual)
