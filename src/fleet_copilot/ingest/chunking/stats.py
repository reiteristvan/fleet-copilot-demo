"""Measure what each strategy produced.

A mean would hide what these strategies are built around. Table and step-list
chunks are as long as they need to be, so the distribution is bimodal by
construction. Percentiles show that. An average conceals it.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from fleet_copilot.ingest.chunking.structural import is_step_line
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk, StrategyId


class StrategyStats(BaseModel):
    """One row of the published table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: StrategyId
    documents: int
    chunks: int
    min_tokens: int
    median_tokens: int
    p90_tokens: int
    max_tokens: int
    chunks_per_document: float
    table_chunks: int
    split_step_lists: int


TABLE_OPENERS: tuple[str, ...] = ("|", "<table")
"""How a table chunk begins, in either dialect the corpus produces.

The Markdown parser emits pipe rows straight from the source. Document
Intelligence emits HTML even in Markdown mode. Counting only pipes would report
the converted documents as having no tables. That is the one construct ADR 0006
gives its own chunk type. The chunkers split on the block role and never read
this.
"""


def _is_table(text: str) -> bool:
    return text.lstrip().lower().startswith(TABLE_OPENERS)


def summarise(
    strategy: StrategyId, chunks: Sequence[Chunk], counter: TokenCounter | None = None
) -> StrategyStats:
    """Reduce a strategy's chunks to the row that describes them.

    ``split_step_lists`` counts chunks that begin or end partway through an
    ordered list. It is the metric that shows the atomicity rule working or not
    working. It must be zero for the structural strategies and non-zero for the
    baseline, or the comparison has no contrast to find.
    """
    counter = HeuristicCounter() if counter is None else counter
    if not chunks:
        return StrategyStats(
            strategy=strategy,
            documents=0,
            chunks=0,
            min_tokens=0,
            median_tokens=0,
            p90_tokens=0,
            max_tokens=0,
            chunks_per_document=0.0,
            table_chunks=0,
            split_step_lists=0,
        )

    sizes = sorted(counter.count(chunk.text, chunk.language) for chunk in chunks)
    documents = len({chunk.doc_id for chunk in chunks})

    split = 0
    for chunk in chunks:
        lines = chunk.text.splitlines()
        if not any(is_step_line(line) for line in lines):
            continue
        # A chunk holding steps that neither starts at step 1 nor ends at the
        # list's last step has cut into a procedure.
        first_step = next(line for line in lines if is_step_line(line))
        if not first_step.strip().startswith("1."):
            split += 1

    return StrategyStats(
        strategy=strategy,
        documents=documents,
        chunks=len(chunks),
        min_tokens=sizes[0],
        median_tokens=int(statistics.median(sizes)),
        p90_tokens=sizes[int(0.90 * (len(sizes) - 1))],
        max_tokens=sizes[-1],
        chunks_per_document=round(len(chunks) / documents, 2),
        table_chunks=sum(1 for chunk in chunks if _is_table(chunk.text)),
        split_step_lists=split,
    )


HEADER = (
    "| Strategy | Docs | Chunks | Chunks/doc | Min | Median | p90 | Max | "
    "Table chunks | Split step lists |"
)
DIVIDER = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"


def as_markdown_table(rows: Sequence[StrategyStats]) -> str:
    """Render the rows as the table docs/chunking.md publishes."""
    lines = [HEADER, DIVIDER]
    for row in rows:
        lines.append(
            f"| {row.strategy.value} | {row.documents} | {row.chunks} | "
            f"{row.chunks_per_document} | {row.min_tokens} | {row.median_tokens} | "
            f"{row.p90_tokens} | {row.max_tokens} | {row.table_chunks} | "
            f"{row.split_step_lists} |"
        )
    return "\n".join(lines) + "\n"
