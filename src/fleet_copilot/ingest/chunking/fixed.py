"""The baseline strategy: a sliding window over content, structure ignored.

Deliberately blind. It splits tables and step lists, and it carries no section
path, because story 3.3 has to be able to attribute a retrieval failure to
exactly those things. A baseline that quietly respected structure would make the
comparison flattering and useless.

One concession: windows snap to whitespace. A window ending mid-word embeds a
token sequence no query produces, and the comparison would then be measuring
tokenisation damage rather than boundary placement.
"""

from __future__ import annotations

from typing import Final

from fleet_copilot.ingest.chunking.context import DocumentContext
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk, StrategyId
from fleet_copilot.ingest.parse import ParsedDocument

DEFAULT_TARGET_TOKENS: Final = 220
"""The same target the structural chunker uses.

Held equal on purpose. If the two strategies used different sizes the comparison
would confound size with boundary placement, and the result would say nothing
about structure at all.
"""

DEFAULT_OVERLAP_TOKENS: Final = 40
"""Roughly 18% of the window. Enough that a sentence straddling a boundary
survives in one of the two windows; small enough that the corpus does not
inflate by a fifth."""

SNAP_WINDOW: Final = 60
"""How far back to look for whitespace before giving up and cutting mid-word.

Bounded so a run of 200 characters without a space -- a long table row -- cannot
collapse a window to nothing.
"""


class FixedWindowChunker:
    """Slides a token-budgeted window over content. Implements Chunker."""

    def __init__(
        self,
        target_tokens: int = DEFAULT_TARGET_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
        counter: TokenCounter | None = None,
    ) -> None:
        if overlap_tokens >= target_tokens:
            msg = f"overlap ({overlap_tokens}) must be smaller than the window ({target_tokens})"
            raise ValueError(msg)
        self._target = target_tokens
        self._overlap = overlap_tokens
        self._counter = HeuristicCounter() if counter is None else counter

    @property
    def strategy(self) -> str:
        return StrategyId.FIXED.value

    def _snap(self, content: str, end: int) -> int:
        """Pull ``end`` back to the nearest whitespace, within SNAP_WINDOW."""
        if end >= len(content):
            return len(content)
        for candidate in range(end, max(end - SNAP_WINDOW, 0), -1):
            if content[candidate].isspace():
                return candidate
        return end

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]:
        """Split ``document`` into overlapping windows."""
        content = document.content
        width = self._counter.characters_for(self._target, context.language)
        stride = self._counter.characters_for(self._target - self._overlap, context.language)

        chunks: list[Chunk] = []
        start = 0
        while start < len(content):
            end = self._snap(content, min(start + width, len(content)))
            if end <= start:
                end = min(start + width, len(content))
            text = content[start:end]
            if text.strip():
                chunks.append(
                    Chunk(
                        **context.chunk_fields(),
                        chunk_index=len(chunks),
                        strategy=StrategyId.FIXED,
                        text=text,
                        start=start,
                        end=end,
                    )
                )
            if end >= len(content):
                break
            start += stride
        return tuple(chunks)
