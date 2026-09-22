"""How a chunker decides a chunk is full.

The embedding model bills and limits in tokens, so a token is the honest unit.
Counting them exactly needs tiktoken, which downloads its BPE table over HTTPS
the first time it is asked for an encoding -- so it cannot be what `just check`
runs, and it cannot be what decides a chunk boundary if boundaries are to be
identical on every machine.

The default is therefore a per-language ratio, measured once against the real
tokenizer over this corpus and held to it by a committed fixture.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final, Protocol, runtime_checkable

from fleet_copilot.corpus.models import Language

CHARS_PER_TOKEN: Final[Mapping[Language, float]] = {
    Language.EN: 4.17,
    Language.HU: 2.21,
}
"""Median characters per cl100k_base token, measured over the whole corpus.

English 4.17 (n=107), Hungarian 2.21 (n=13). The gap is not noise: cl100k_base
was trained overwhelmingly on English, and an agglutinative language fragments
into far more subword pieces. A single global ratio would let every Hungarian
chunk run to nearly twice the token budget the chunker thought it had set.

Both are medians of per-document ratios, measured by
`scripts/capture_token_counts.py` -- the script to re-run after a corpus change.
`just chunk-stats --calibrate` is a cross-check rather than a regeneration: it
measures chunks, not documents, so its numbers land near these without matching
them.
"""

DEFAULT_CHARS_PER_TOKEN: Final = 4.17
"""Used for a language not in the table. English, because that is the corpus's
majority and an under-estimate here produces chunks that are too large, which
is the failure that shows up rather than the one that hides."""


@runtime_checkable
class TokenCounter(Protocol):
    """Estimates how many tokens a string will cost."""

    def count(self, text: str, language: Language) -> int: ...

    def characters_for(self, tokens: int, language: Language) -> int: ...


class HeuristicCounter:
    """Characters divided by a per-language ratio. Implements TokenCounter.

    Deterministic, offline and dependency-free, which is what makes chunk
    boundaries identical in CI, on a laptop and in a container. It is an
    estimate: `tests/ingest/chunking/test_tokens.py` holds it to within 15% of
    the real tokenizer at the median.
    """

    def _ratio(self, language: Language) -> float:
        return CHARS_PER_TOKEN.get(language, DEFAULT_CHARS_PER_TOKEN)

    def count(self, text: str, language: Language) -> int:
        """Estimate the token cost of ``text``.

        Rounds up: a non-empty string that costs zero tokens would let a window
        accept text without ever filling.
        """
        if not text:
            return 0
        return max(1, math.ceil(len(text) / self._ratio(language)))

    def characters_for(self, tokens: int, language: Language) -> int:
        """The character budget that corresponds to ``tokens``."""
        return max(1, int(tokens * self._ratio(language)))


class TiktokenCounter:
    """Exact cl100k_base counts. Implements TokenCounter.

    Never used to decide a boundary -- it needs a network round trip the first
    time it runs, and a chunk boundary that depends on whether a download
    succeeded is not a boundary. It is used by chunk_stats to report true sizes
    for chunks whose boundaries the heuristic chose, and to recalibrate the
    ratios above.
    """

    def __init__(self) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str, language: Language) -> int:
        return len(self._encoding.encode(text))

    def characters_for(self, tokens: int, language: Language) -> int:
        """Approximate, and only meaningful in aggregate.

        There is no exact inverse of a BPE encoding, which is the other reason
        boundaries are decided by the heuristic.
        """
        return max(1, int(tokens * CHARS_PER_TOKEN.get(language, DEFAULT_CHARS_PER_TOKEN)))
