"""Reading and writing the cache."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_copilot.ingest.embedding.models import EmbeddingRecord
from fleet_copilot.ingest.embedding.store import EmbeddingStore

MODEL = "test-embeddings"
"""Deliberately not the real deployment name.

put_many commits -- the store owns its own transaction so an interrupted run
keeps what it paid for (ADR 0007) -- so these rows outlive the test and share a
table with a real corpus run. The cache key is (content_hash, model_id), so a
model id no deployment will ever have cannot collide by construction.

Isolating by key rather than by a teardown is the point: a delete runs only if
the test got that far, while a key that cannot match is safe even when the test
dies halfway. Written against the live name once, this left four fake vectors
next to 1,476 real ones, found only because a row count failed to add up.
"""
DIMENSIONS = 3072


def a_record(seed: str, dimensions: int = DIMENSIONS) -> EmbeddingRecord:
    return EmbeddingRecord(
        content_hash=seed * 64,
        model_id=MODEL,
        dimensions=dimensions,
        embedding=tuple(0.0 for _ in range(dimensions)),
    )


def test_a_record_rejects_a_length_that_disagrees_with_its_own_field() -> None:
    """A vector whose length does not match its declared dimensions would be
    rejected by Postgres with a message about the column, three layers from the
    code that built it."""
    with pytest.raises(ValidationError, match="dimensions"):
        EmbeddingRecord.model_validate(
            {
                "content_hash": "a" * 64,
                "model_id": MODEL,
                "dimensions": 4,
                "embedding": (0.0, 0.0),
            }
        )


def test_a_record_rejects_a_hash_that_is_not_a_sha256() -> None:
    """The same constraint the table carries, enforced before the round trip."""
    with pytest.raises(ValidationError):
        EmbeddingRecord.model_validate(
            {
                "content_hash": "not-a-hash",
                "model_id": MODEL,
                "dimensions": 1,
                "embedding": (0.0,),
            }
        )


@pytest.mark.asyncio
async def test_what_goes_in_is_known_afterwards(database_url: str) -> None:
    store = EmbeddingStore(database_url)

    written = await store.put_many([a_record("a"), a_record("b")])

    assert written == 2
    assert await store.known({"a" * 64, "b" * 64, "c" * 64}, MODEL) == {"a" * 64, "b" * 64}


@pytest.mark.asyncio
async def test_writing_the_same_hash_twice_is_not_an_error(database_url: str) -> None:
    """Two strategies can produce the same chunk text with no header, and a run
    interrupted and restarted will re-offer what it already wrote."""
    store = EmbeddingStore(database_url)

    await store.put_many([a_record("d")])
    await store.put_many([a_record("d")])

    assert await store.known({"d" * 64}, MODEL) == {"d" * 64}


@pytest.mark.asyncio
async def test_another_model_does_not_see_these_rows(database_url: str) -> None:
    store = EmbeddingStore(database_url)

    await store.put_many([a_record("e")])

    assert await store.known({"e" * 64}, "some-other-deployment") == set()


@pytest.mark.asyncio
async def test_asking_about_nothing_touches_no_database(database_url: str) -> None:
    """An empty batch is common at the end of a run; a round trip for it is
    pure latency."""
    assert await EmbeddingStore(database_url).known(set(), MODEL) == set()
