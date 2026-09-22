"""What every chunking strategy is, independently of how it splits."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fleet_copilot.ingest.chunking.context import DocumentContext
from fleet_copilot.ingest.models import Chunk
from fleet_copilot.ingest.parse import ParsedDocument


@runtime_checkable
class Chunker(Protocol):
    """Splits one parsed document into chunks.

    Synchronous and pure: a strategy that reached for anything outside its two
    arguments could not be compared against another one run on the same input,
    which is the only thing story 3.3 does with these.
    """

    @property
    def strategy(self) -> str: ...

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]: ...
