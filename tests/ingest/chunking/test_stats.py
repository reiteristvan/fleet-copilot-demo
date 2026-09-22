"""The numbers that go in docs/chunking.md."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
from fleet_copilot.ingest.chunking.stats import as_markdown_table, summarise
from fleet_copilot.ingest.chunking.structural import StructuralChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_it_reports_a_distribution_not_just_a_mean() -> None:
    """A mean hides the bimodality the atomicity rules create on purpose."""
    document, context = await the_manual()
    chunks = StructuralChunker().chunk(document, context)

    stats = summarise(StrategyId.STRUCTURAL, chunks)

    assert stats.chunks == len(chunks)
    assert stats.documents == 1
    assert stats.min_tokens <= stats.median_tokens <= stats.max_tokens
    assert stats.p90_tokens >= stats.median_tokens


@pytest.mark.asyncio
async def test_it_counts_split_step_lists_because_that_is_the_headline() -> None:
    """The metric that shows the atomicity rule working, or not working.

    Structural must be zero. The baseline must not be, or it is not splitting
    anything and the comparison in 3.3 has no contrast to find.
    """
    document, context = await the_manual()

    structural = summarise(
        StrategyId.STRUCTURAL, StructuralChunker(target_tokens=40).chunk(document, context)
    )
    fixed = summarise(
        StrategyId.FIXED,
        FixedWindowChunker(target_tokens=40, overlap_tokens=8).chunk(document, context),
    )

    assert structural.split_step_lists == 0
    assert fixed.split_step_lists > 0


@pytest.mark.asyncio
async def test_the_table_renders_one_row_per_strategy() -> None:
    document, context = await the_manual()
    rows = [
        summarise(StrategyId.FIXED, FixedWindowChunker().chunk(document, context)),
        summarise(StrategyId.STRUCTURAL, StructuralChunker().chunk(document, context)),
    ]

    table = as_markdown_table(rows)

    assert table.startswith("| Strategy |")
    assert "| fixed |" in table
    assert "| structural |" in table
    assert table.endswith("\n")
