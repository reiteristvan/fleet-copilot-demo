"""How a chunker decides a chunk is full.

The embedding model bills and limits in tokens, so a token is the honest unit.

Counting tokens exactly needs tiktoken. It downloads its BPE table over HTTPS
the first time it is asked for an encoding. So it cannot run inside `just check`,
and it cannot decide a chunk boundary. Boundaries must be identical on every
machine.

The default is a per-language ratio. It was measured once against the real
tokenizer over this corpus, and a committed fixture holds it there.
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

English is 4.17 (n=107). Hungarian is 2.21 (n=13). The gap is not noise.
cl100k_base was trained mostly on English, and an agglutinative language
fragments into far more subword pieces. One global ratio would let every
Hungarian chunk run to nearly twice its intended budget.

Both values are medians of per-document ratios. `scripts/capture_token_counts.py`
measured them, and that is the script to re-run after a corpus change.
`just chunk-stats --calibrate` cross-checks rather than regenerates. It measures
chunks where these are per-document, so its numbers land near these without
matching them.
"""

DEFAULT_CHARS_PER_TOKEN: Final = 4.17
"""Used for a language not in the table.

English, because that is the corpus majority. An under-estimate here produces
chunks that are too large. That failure shows up. The opposite one hides.
"""


@runtime_checkable
class TokenCounter(Protocol):
    """Estimates how many tokens a string will cost."""

    def count(self, text: str, language: Language) -> int: ...

    def characters_for(self, tokens: int, language: Language) -> int: ...


class HeuristicCounter:
    """Characters divided by a per-language ratio. Implements TokenCounter.

    Deterministic, offline and dependency-free. That is what makes chunk
    boundaries identical in CI, on a laptop and in a container.

    It is an estimate. `tests/ingest/chunking/test_tokens.py` holds it to within
    15% of the real tokenizer at the median.
    """

    def _ratio(self, language: Language) -> float:
        return CHARS_PER_TOKEN.get(language, DEFAULT_CHARS_PER_TOKEN)

    def count(self, text: str, language: Language) -> int:
        """Estimate the token cost of ``text``.

        Rounds up. A non-empty string that cost zero tokens would let a window
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

    Never decides a boundary. It needs a network round trip the first time it
    runs, and a boundary that depends on a download is not a boundary.

    chunk_stats uses it to report true sizes for chunks the heuristic bounded,
    and to recalibrate the ratios above.
    """

    def __init__(self) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str, language: Language) -> int:
        return len(self._encoding.encode(text))

    def characters_for(self, tokens: int, language: Language) -> int:
        """Approximate, and only meaningful in aggregate.

        A BPE encoding has no exact inverse. That is the other reason the
        heuristic decides boundaries.
        """
        return max(1, int(tokens * CHARS_PER_TOKEN.get(language, DEFAULT_CHARS_PER_TOKEN)))
