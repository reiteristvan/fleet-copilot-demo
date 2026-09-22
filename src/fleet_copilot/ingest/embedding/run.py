"""Embed a set of chunks, skipping everything already cached.

Deduplicated before anything is sent: three strategies produce chunks over the
same documents, and two of them can produce byte-identical embed_text. Paying
twice for one vector is the small cost; writing two rows that must stay in step
is the larger one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from typing import Protocol

from fleet_copilot.ingest.embedding.batching import batch_by_tokens
from fleet_copilot.ingest.embedding.client import Embedder
from fleet_copilot.ingest.embedding.models import EmbeddingRecord, EmbedReport
from fleet_copilot.ingest.models import Chunk

DEFAULT_CONCURRENCY = 4
"""Requests in flight at once.

Four batches of 8000 tokens is 32,000 tokens outstanding against a 50,000
tokens-per-minute deployment, which leaves headroom for the retry the SDK will
make if one of them is throttled anyway.
"""


class Store(Protocol):
    """The part of EmbeddingStore this module needs."""

    async def known(self, hashes: Collection[str], model_id: str) -> set[str]: ...

    async def put_many(self, records: Sequence[EmbeddingRecord]) -> int: ...


async def embed_chunks(
    chunks: Sequence[Chunk],
    store: Store,
    embedder: Embedder,
    *,
    dimensions: int,
    budget_tokens: int,
    dry_run: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> EmbedReport:
    """Embed whatever is not cached, and report what happened.

    ``dry_run`` reports the work without calling the model or writing a row,
    which is what makes the first line of a run inspectable before it spends
    anything.
    """
    by_hash: dict[str, Chunk] = {}
    for chunk in chunks:
        by_hash.setdefault(chunk.content_hash, chunk)

    cached = await store.known(set(by_hash), embedder.model_id)
    pending = [chunk for digest, chunk in by_hash.items() if digest not in cached]
    batches = batch_by_tokens(pending, budget_tokens=budget_tokens)

    if dry_run:
        return EmbedReport(
            chunks=len(chunks),
            unique=len(by_hash),
            cached=len(cached),
            embedded=len(pending),
            requests=len(batches),
            dry_run=True,
        )

    limit = asyncio.Semaphore(concurrency)

    async def run_batch(batch: tuple[Chunk, ...]) -> int:
        async with limit:
            vectors = await embedder.embed([chunk.embed_text for chunk in batch])
        if len(vectors) != len(batch):
            msg = f"asked for {len(batch)} vectors and got {len(vectors)}"
            raise RuntimeError(msg)
        # Written per batch, not at the end: a run interrupted halfway keeps
        # what it paid for, and the schema would have to change to add this
        # later.
        return await store.put_many(
            [
                EmbeddingRecord(
                    content_hash=chunk.content_hash,
                    model_id=embedder.model_id,
                    dimensions=dimensions,
                    embedding=vector,
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ]
        )

    written = await asyncio.gather(*(run_batch(batch) for batch in batches))

    return EmbedReport(
        chunks=len(chunks),
        unique=len(by_hash),
        cached=len(cached),
        embedded=sum(written),
        requests=len(batches),
        dry_run=False,
    )
