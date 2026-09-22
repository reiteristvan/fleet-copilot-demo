"""The run, and the criterion the story is judged on."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date

import pytest

from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.ingest.embedding.client import StubEmbedder
from fleet_copilot.ingest.embedding.models import EmbeddingRecord
from fleet_copilot.ingest.embedding.run import embed_chunks
from fleet_copilot.ingest.models import Chunk, StrategyId

DIMENSIONS = 8


class MemoryStore:
    """An EmbeddingStore with the database taken out."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EmbeddingRecord] = {}

    async def known(self, hashes: Collection[str], model_id: str) -> set[str]:
        return {digest for digest in hashes if (digest, model_id) in self.rows}

    async def put_many(self, records: Sequence[EmbeddingRecord]) -> int:
        for record in records:
            self.rows[(record.content_hash, record.model_id)] = record
        return len(records)


def some_chunks(count: int = 12) -> list[Chunk]:
    def body(index: int) -> str:
        return f"passage number {index} " + "x" * 200

    return [
        Chunk(
            doc_id="d",
            type=DocumentType.SERVICE_MANUAL,
            language=Language.EN,
            revision=1,
            effective_date=date(2026, 1, 1),
            chunk_index=index,
            strategy=StrategyId.STRUCTURAL,
            text=body(index),
            start=0,
            end=len(body(index)),
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_a_clean_rerun_makes_zero_embedding_calls() -> None:
    """The story's acceptance criterion, in one test.

    The corpus is deterministic and the chunkers are pure, so the second run
    must find every hash already present. A single call here means the key does
    not describe what was embedded.
    """
    chunks = some_chunks()
    store = MemoryStore()

    first = await embed_chunks(
        chunks, store, StubEmbedder(DIMENSIONS), dimensions=DIMENSIONS, budget_tokens=200
    )

    second_embedder = StubEmbedder(DIMENSIONS)
    second = await embed_chunks(
        chunks, store, second_embedder, dimensions=DIMENSIONS, budget_tokens=200
    )

    assert first.embedded == len(chunks)
    assert second_embedder.calls == 0
    assert second.embedded == 0
    assert second.cached == len(chunks)


@pytest.mark.asyncio
async def test_identical_text_is_embedded_once() -> None:
    """Two chunks with the same embed_text share a cache key by construction."""
    chunk = some_chunks(1)[0]
    twin = chunk.model_copy(update={"chunk_index": 99})
    embedder = StubEmbedder(DIMENSIONS)

    report = await embed_chunks(
        [chunk, twin], MemoryStore(), embedder, dimensions=DIMENSIONS, budget_tokens=200
    )

    assert report.chunks == 2
    assert report.unique == 1
    assert embedder.inputs == 1


@pytest.mark.asyncio
async def test_a_dry_run_calls_nothing_and_writes_nothing() -> None:
    embedder = StubEmbedder(DIMENSIONS)
    store = MemoryStore()

    report = await embed_chunks(
        some_chunks(), store, embedder, dimensions=DIMENSIONS, budget_tokens=200, dry_run=True
    )

    assert embedder.calls == 0
    assert store.rows == {}
    assert report.dry_run
    assert report.embedded == len(some_chunks())


@pytest.mark.asyncio
async def test_the_vector_stored_is_the_one_for_that_hash() -> None:
    """A batch whose vectors were zipped to the wrong chunks would still produce
    a full cache and a plausible report."""
    chunks = some_chunks(3)
    store = MemoryStore()
    embedder = StubEmbedder(DIMENSIONS)

    await embed_chunks(chunks, store, embedder, dimensions=DIMENSIONS, budget_tokens=200)

    expected = await StubEmbedder(DIMENSIONS).embed([chunks[1].embed_text])
    assert store.rows[(chunks[1].content_hash, "stub")].embedding == expected[0]


@pytest.mark.asyncio
async def test_no_chunks_is_a_report_and_no_calls() -> None:
    embedder = StubEmbedder(DIMENSIONS)

    report = await embed_chunks(
        [], MemoryStore(), embedder, dimensions=DIMENSIONS, budget_tokens=200
    )

    assert embedder.calls == 0
    assert report.chunks == 0


@pytest.mark.asyncio
async def test_a_short_vector_is_refused_rather_than_stored() -> None:
    """The embedder's width and the column's must agree. A mismatch caught here
    names the chunk; caught in Postgres it names the column."""
    with pytest.raises(ValueError, match="dimensions"):
        await embed_chunks(
            some_chunks(1),
            MemoryStore(),
            StubEmbedder(DIMENSIONS),
            dimensions=DIMENSIONS + 1,
            budget_tokens=200,
        )
