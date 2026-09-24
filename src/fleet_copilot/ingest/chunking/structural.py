"""Chunk on the structure the parser recovered.

Two rules override the size target. Both come from ADR 0006.

A table is its own chunk. An interval table answers a different question from
the prose around it, and a row separated from its column headers is noise.

An ordered step list is never split. Half a procedure reads exactly like a whole
one.

Both rules make the size distribution bimodal on purpose. A stats table that
shows otherwise means they are not being applied.
"""

from __future__ import annotations

import re
from typing import Final

from fleet_copilot.corpus.models import Language
from fleet_copilot.ingest.chunking.context import (
    DocumentContext,
    contextual_header,
    section_path_at,
)
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk, StrategyId
from fleet_copilot.ingest.parse import HEADING_ROLES, BlockRole, ParsedBlock, ParsedDocument

DEFAULT_TARGET_TOKENS: Final = 220
"""Chosen against the corpus, not from convention.

The median document is 183 tokens. The usual 512 would leave 108 of 120
documents as one chunk, and the three-way comparison would measure nothing. 220
sits above the median document and below the p75. It splits the long documents
and leaves the short ones whole.
"""

STEP_LINE = re.compile(r"^\s*\d+\.\s")
"""An ordered-list line.

Bullets are not matched. A bullet list is a set of independent statements, so
splitting one costs a little context. A procedure is a sequence, and half of one
is dangerous.
"""

SKIPPED_ROLES: Final = frozenset(
    {BlockRole.PAGE_HEADER, BlockRole.PAGE_FOOTER, BlockRole.PAGE_NUMBER}
)
"""Page furniture.

The parser keeps it in content so offsets stay stable. This module skips it,
because a chunk of a page number retrieves nothing and costs an embedding.
"""


def is_step_line(line: str) -> bool:
    """Whether ``line`` is a numbered step."""
    return bool(STEP_LINE.match(line))


def _has_steps(text: str) -> bool:
    return any(is_step_line(line) for line in text.splitlines())


class StructuralChunker:
    """Packs blocks under their heading, up to a token target."""

    def __init__(
        self, target_tokens: int = DEFAULT_TARGET_TOKENS, counter: TokenCounter | None = None
    ) -> None:
        self._target = target_tokens
        self._counter = HeuristicCounter() if counter is None else counter

    @property
    def strategy(self) -> str:
        return StrategyId.STRUCTURAL.value

    def _emit(
        self,
        document: ParsedDocument,
        context: DocumentContext,
        start: int,
        end: int,
        index: int,
    ) -> Chunk:
        return Chunk(
            **context.chunk_fields(),
            section_path=section_path_at(document, start),
            chunk_index=index,
            strategy=StrategyId.STRUCTURAL,
            text=document.content[start:end],
            start=start,
            end=end,
        )

    def _is_atomic(self, block: ParsedBlock) -> bool:
        """Whether ``block`` must be a chunk on its own, whatever its size."""
        return block.role is BlockRole.TABLE or _has_steps(block.text)

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]:
        """Split ``document``. Never returns empty for a non-empty document."""
        language: Language = context.language
        chunks: list[Chunk] = []
        run_start: int | None = None
        run_end = 0
        run_tokens = 0

        def flush() -> None:
            nonlocal run_start, run_tokens
            if run_start is not None:
                chunks.append(self._emit(document, context, run_start, run_end, len(chunks)))
                run_start = None
                run_tokens = 0

        for block in document.blocks:
            if block.role in SKIPPED_ROLES:
                continue

            # A heading opens a new chunk rather than joining the one before it:
            # it describes what follows, and trailing it onto the previous
            # section puts it in the wrong breadcrumb.
            if block.role in HEADING_ROLES or self._is_atomic(block):
                flush()

            cost = self._counter.count(block.text, language)
            if run_start is not None and run_tokens + cost > self._target:
                flush()

            if run_start is None:
                run_start = block.start
            run_end = block.end
            run_tokens += cost

            if self._is_atomic(block):
                flush()

        flush()
        return tuple(chunks)


class ContextualChunker:
    """The structural strategy plus a header. Implements Chunker.

    A wrapper rather than a copy, so the two strategies cannot drift apart.

    They must cut in exactly the same places. If they did not, story 3.3 could
    not tell a header effect from a boundary effect, and the experiment would
    have two variables instead of one.
    """

    def __init__(self, structural: StructuralChunker | None = None) -> None:
        self._structural = StructuralChunker() if structural is None else structural

    @property
    def strategy(self) -> str:
        return StrategyId.CONTEXTUAL.value

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]:
        """Re-emit the structural chunks, each carrying its breadcrumb."""
        return tuple(
            base.model_copy(
                update={
                    "strategy": StrategyId.CONTEXTUAL,
                    # A chunk before the first heading in a document with no
                    # machine types leaves the header empty, and Chunk rejects a
                    # blank prefix. The doc_id is the weakest useful header
                    # rather than a crash.
                    "context_prefix": contextual_header(context, base.section_path)
                    or context.doc_id,
                }
            )
            for base in self._structural.chunk(document, context)
        )
