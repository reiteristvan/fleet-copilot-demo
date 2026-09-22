"""How chunks are grouped into requests."""

from __future__ import annotations

from datetime import date

import pytest

from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.ingest.embedding.batching import (
    MAX_INPUTS_PER_REQUEST,
    OversizedChunkError,
    batch_by_tokens,
)
from fleet_copilot.ingest.models import Chunk, StrategyId


def a_chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        doc_id="d",
        type=DocumentType.SERVICE_MANUAL,
        language=Language.EN,
        revision=1,
        effective_date=date(2026, 1, 1),
        chunk_index=index,
        strategy=StrategyId.STRUCTURAL,
        text=text,
        start=0,
        end=len(text),
    )


def test_a_batch_stops_at_the_token_budget_not_at_a_count() -> None:
    """The throttle is on tokens per minute, so tokens are what a batch counts.

    Batching by count sends a wildly variable number of tokens per request --
    this corpus has chunks from 40 tokens to over 1000 -- so the request that
    trips the limit is unpredictable and unrelated to anything a reader can see.
    """
    chunks = [a_chunk("x" * 417, i) for i in range(10)]  # ~100 tokens each at 4.17

    batches = batch_by_tokens(chunks, budget_tokens=250)

    assert all(len(batch) <= 3 for batch in batches)
    assert sum(len(batch) for batch in batches) == 10


def test_every_chunk_appears_exactly_once_and_in_order() -> None:
    chunks = [a_chunk("x" * 100, i) for i in range(50)]

    batches = batch_by_tokens(chunks, budget_tokens=200)

    flattened = [chunk for batch in batches for chunk in batch]
    assert [chunk.chunk_index for chunk in flattened] == list(range(50))


def test_a_batch_never_exceeds_the_per_request_input_cap() -> None:
    """Azure rejects more than 2048 inputs whatever the token count."""
    chunks = [a_chunk("x", i) for i in range(MAX_INPUTS_PER_REQUEST + 10)]

    batches = batch_by_tokens(chunks, budget_tokens=10_000_000)

    assert all(len(batch) <= MAX_INPUTS_PER_REQUEST for batch in batches)
    assert len(batches) == 2


def test_a_chunk_larger_than_the_budget_still_gets_its_own_batch() -> None:
    """Dropping it would silently lose a document's longest passage, which is
    usually the table or the procedure that mattered."""
    chunks = [a_chunk("x" * 4170)]  # ~1000 tokens

    batches = batch_by_tokens(chunks, budget_tokens=250)

    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_a_chunk_over_the_model_limit_is_refused_loudly() -> None:
    """8191 tokens is the model's hard cap. Truncating here would embed a
    prefix and cache it under a hash of the whole thing -- a wrong vector that
    every later run would happily reuse."""
    with pytest.raises(OversizedChunkError, match="8191"):
        batch_by_tokens([a_chunk("x" * 40_000)], budget_tokens=250)


def test_no_chunks_is_no_batches() -> None:
    assert batch_by_tokens([], budget_tokens=250) == ()


def test_the_contextual_header_counts_towards_the_budget() -> None:
    """A batch is sized by what is sent, and what is sent is embed_text.

    Counting text would under-report every contextual chunk by the length of
    its breadcrumb, and the budget would drift by exactly the amount strategy 3
    adds -- on the one strategy whose requests are largest.
    """
    plain = a_chunk("x" * 417)
    prefixed = plain.model_copy(
        update={"strategy": StrategyId.CONTEXTUAL, "context_prefix": "y" * 417}
    )

    assert len(batch_by_tokens([prefixed, prefixed], budget_tokens=250)) == 2
    assert len(batch_by_tokens([plain, plain], budget_tokens=250)) == 1
