"""Group chunks into embedding requests.

By tokens, not by count. The Azure throttle is tokens per minute, and this
corpus holds chunks from 40 tokens to over a thousand -- so a batch of a fixed
number of inputs sends an unpredictable number of tokens, and the request that
finally trips the limit has nothing to do with anything a reader can see.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk

MAX_INPUTS_PER_REQUEST: Final = 2048
"""Azure's cap on array length for one embeddings request, whatever the tokens."""

MAX_TOKENS_PER_INPUT: Final = 8191
"""The model's per-input cap. A chunk above it cannot be embedded at all."""


class OversizedChunkError(ValueError):
    """Raised when a chunk cannot fit in a single embedding request."""


def batch_by_tokens(
    chunks: Sequence[Chunk],
    *,
    budget_tokens: int,
    max_inputs: int = MAX_INPUTS_PER_REQUEST,
    counter: TokenCounter | None = None,
) -> tuple[tuple[Chunk, ...], ...]:
    """Split ``chunks`` into requests, preserving order.

    Sized on ``embed_text`` rather than ``text``: the header is part of what is
    sent, so counting the slice alone would under-report every contextual chunk
    by its breadcrumb -- on the one strategy whose requests are largest.

    A chunk larger than ``budget_tokens`` gets a batch of its own rather than
    being dropped: it is usually the table or the procedure that mattered, and
    losing it silently is worse than one oversized request.

    A chunk over the model's own cap raises. Truncating it here would embed a
    prefix and store the vector under a hash of the whole text -- a wrong answer
    that every later run would reuse without ever calling the model again.
    """
    counter = HeuristicCounter() if counter is None else counter

    batches: list[tuple[Chunk, ...]] = []
    current: list[Chunk] = []
    current_tokens = 0

    for chunk in chunks:
        cost = counter.count(chunk.embed_text, chunk.language)
        if cost > MAX_TOKENS_PER_INPUT:
            msg = (
                f"{chunk.chunk_id} is about {cost} tokens, over the model's "
                f"{MAX_TOKENS_PER_INPUT} limit for a single input"
            )
            raise OversizedChunkError(msg)

        too_many = len(current) >= max_inputs
        too_large = bool(current) and current_tokens + cost > budget_tokens
        if too_many or too_large:
            batches.append(tuple(current))
            current = []
            current_tokens = 0

        current.append(chunk)
        current_tokens += cost

    if current:
        batches.append(tuple(current))
    return tuple(batches)
