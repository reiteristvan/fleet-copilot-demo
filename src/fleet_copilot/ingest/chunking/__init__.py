"""Split a parsed document into the units the retriever indexes."""

from fleet_copilot.ingest.chunking.base import Chunker, chunkers
from fleet_copilot.ingest.chunking.context import DocumentContext
from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
from fleet_copilot.ingest.chunking.structural import ContextualChunker, StructuralChunker

__all__ = [
    "Chunker",
    "ContextualChunker",
    "DocumentContext",
    "FixedWindowChunker",
    "StructuralChunker",
    "chunkers",
]
