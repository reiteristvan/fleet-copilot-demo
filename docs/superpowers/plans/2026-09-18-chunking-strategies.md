# Chunking Strategies — Implementation Plan (2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn every parsed document into chunks under three strategies — fixed-size with overlap, heading-aware structural, and structural with a contextual header — each chunk carrying the ten metadata fields ADR 0006 requires, and publish a size distribution per strategy in `docs/chunking.md`.

**Architecture:** A `Chunker` Protocol over `ParsedDocument`. `StructuralChunker` packs blocks under their heading, emits each table as its own chunk and never splits an ordered step list. `ContextualChunker` *wraps* it and adds only a `context_prefix`, so strategies 2 and 3 share boundaries exactly and the A/B measures the header alone. `FixedWindowChunker` ignores blocks entirely and slides a token window over `content` — the honest baseline. Token budgets go through a `TokenCounter` Protocol whose default is a per-language heuristic, so chunk boundaries are deterministic and CI never reaches the network.

**Tech Stack:** Python 3.12, pydantic v2 (frozen, `extra="forbid"`), pytest + pytest-asyncio, mypy --strict, ruff. `tiktoken` is an optional dev extra used only to report exact sizes and to regenerate a committed fixture; nothing in `src` requires it at import time.

**Spec:** `docs/adr/0006-the-chunk-contract.md`, and `docs/adr/0005-document-parsing-and-the-layout-cache.md` for what a chunker reads.

**Depends on Plan 1** (`docs/superpowers/plans/2026-09-17-parsing-and-the-layout-cache.md`) being merged: `ParsedDocument`, the three parsers, manifest v2 and the layout cache.

**Scope boundary.** This plan produces chunks and measures them. It makes **no embedding call**. `content_hash` is computed here because it is the embedding cache key and ADR 0006 fixes what it hashes, but nothing in this plan sends anything to a model.

- **Plan 3 — embeddings:** `text-embedding-3-large`, batching, async, 429 backoff, the Postgres cache keyed by `content_hash`, and the "zero embedding calls on a clean re-run" criterion.

## Global Constraints

From `CLAUDE.md` and ADR 0006. Every task's requirements implicitly include this section.

- **`just check` is the gate.** Paste its output; never assert it passed.
- **Never weaken a gate to make a change land.** No new ruff ignores, no relaxed mypy settings, no `xfail` to get green.
- **Every `noqa` and `type: ignore` carries a code and a reason.**
- **Do not touch without an explicit human go-ahead:** the `[tool.*]` sections of `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/`, `uv.lock` by hand, `CLAUDE.md`. Adding to `[dependency-groups].dev` is in scope; relock with `uv`.
- **Python 3.12 floor.** `X | None`, built-in generics, `Self`, `datetime.UTC`, `StrEnum`.
- **`mypy --strict` covers `src` and `tests`.** Every function annotated, `-> None` on tests.
- **Domain models are pydantic v2, frozen, `extra="forbid"`.** Each validator's docstring says which failure it prevents.
- **Nothing blocking runs in a coroutine.** Chunking itself is pure and synchronous; only the code that reads files is async.
- **Tests mirror the source layout.** `src/fleet_copilot/ingest/chunking/x.py` → `tests/ingest/chunking/test_x.py`.
- **Test invalid input through `model_validate`.**
- **Comment only what the code cannot say itself.**
- **Commits are atomic and semantic.** One concern per commit; the body says *why*.
- **`content_hash` is the SHA-256 of `embed_text`, never of `text`** (ADR 0006). Strategies 2 and 3 produce the same slice with different headers; hashing `text` would give them one embedding cache key between them and score strategy 3 on strategy 2's vectors.
- **Chunk metadata comes from `ManifestEntry`, not from the parsed document.** Rendering strips the front matter; manifest v2 carries it.
- **CI never reaches the network.** `tiktoken` downloads its BPE table over HTTPS on first use, so it is never on a code path `just check` executes.

## Measurements this plan is built on

Taken over all 120 Markdown sources with `cl100k_base`, front matter excluded. Reproduce with `just chunk-stats --calibrate` (Task 8).

| Quantity | Value |
| --- | --- |
| Documents | 120 |
| Total tokens | 34,205 |
| Tokens per document — min / p25 / median / p75 / p90 / max | 85 / 169 / 184 / 404 / 467 / 1241 |
| Characters per token, English (n=107) | 4.17 |
| Characters per token, Hungarian (n=13) | **2.21** |

Two consequences drive design decisions below, and both belong in `docs/chunking.md`:

- **A single global characters-per-token ratio is wrong.** Hungarian is agglutinative and `cl100k_base` was not built for it; the same character budget buys 1.9× the tokens. `HeuristicCounter` therefore holds one ratio per language (Task 2).
- **A conventional 512-token window would split 12 of 120 documents.** The other 108 would be a single chunk under every strategy, and the three-way comparison would be measuring nothing. The fixed window is sized from the measured structural distribution instead of from convention (Task 5).

## File Structure

| File | Responsibility |
| --- | --- |
| `src/fleet_copilot/ingest/models.py` (modify) | `Chunk` gains the ADR 0006 contract; `ordinal` becomes `chunk_index`. |
| `src/fleet_copilot/ingest/chunking/__init__.py` (new) | Re-exports the Protocol and the three strategies. |
| `src/fleet_copilot/ingest/chunking/tokens.py` (new) | `TokenCounter`, `HeuristicCounter`, `TiktokenCounter`. |
| `src/fleet_copilot/ingest/chunking/context.py` (new) | `DocumentContext` — metadata + `section_path` derivation. |
| `src/fleet_copilot/ingest/chunking/structural.py` (new) | `StructuralChunker` and `ContextualChunker`. |
| `src/fleet_copilot/ingest/chunking/fixed.py` (new) | `FixedWindowChunker`. |
| `src/fleet_copilot/ingest/chunking/base.py` (new) | `Chunker` Protocol, `StrategyId`, `CHUNKERS`. |
| `src/fleet_copilot/ingest/run.py` (modify) | `parse_all()` — yields every `ParsedDocument` paired with its manifest entry. |
| `scripts/chunk_stats.py` (new) | Argument-parsing shim. |
| `src/fleet_copilot/ingest/chunking/stats.py` (new) | The distribution table, as a pure function. |
| `docs/chunking.md` (new) | The published table and the decisions behind it. |
| `tests/ingest/chunking/fixtures/token_counts.json` (new) | Exact `cl100k_base` counts for a sample, captured once. |

Task order is dependency order. Nothing in this plan needs an Azure account except Task 7's full-corpus run, which reads the layout cache Plan 1 populated.

---

### Task 1: The chunk contract

ADR 0006's ten fields, plus the three the chunk itself needs.

**Files:**
- Modify: `src/fleet_copilot/ingest/models.py:60-100`
- Test: `tests/ingest/test_models.py`

**Interfaces:**
- Consumes: `DocumentType`, `Language` from `fleet_copilot.corpus.models`.
- Produces: `class StrategyId(StrEnum)` with `FIXED = "fixed"`, `STRUCTURAL = "structural"`, `CONTEXTUAL = "contextual"`; `Chunk` with fields `doc_id`, `type`, `machine_types`, `item_numbers`, `language`, `revision`, `effective_date`, `section_path`, `chunk_index`, `strategy`, `text`, `start`, `end`, `context_prefix`, and computed `chunk_id`, `embed_text`, `content_hash`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_models.py`:

```python
def a_chunk(**overrides: object) -> Chunk:
    """Build a valid Chunk, with fields replaced for the case at hand."""
    fields: dict[str, object] = {
        "doc_id": "sdm-43-service-manual",
        "type": "service_manual",
        "machine_types": ("SDM-43",),
        "item_numbers": ("1.291-101.0",),
        "language": "en",
        "revision": 3,
        "effective_date": date(2026, 3, 9),
        "section_path": ("Single-disc machine SDM-43 - service manual", "Safety"),
        "chunk_index": 14,
        "strategy": StrategyId.STRUCTURAL,
        "text": "The emergency stop is not an isolator.",
        "start": 1794,
        "end": 1832,
    }
    fields.update(overrides)
    return Chunk.model_validate(fields)


def test_embed_text_is_the_text_when_there_is_no_prefix() -> None:
    assert a_chunk().embed_text == "The emergency stop is not an isolator."


def test_embed_text_joins_the_prefix_without_disturbing_the_span() -> None:
    chunk = a_chunk(
        strategy=StrategyId.CONTEXTUAL,
        context_prefix=(
            "Single-disc machine SDM-43 - service manual > Safety | SDM-43 | 1.291-101.0"
        ),
    )

    assert chunk.embed_text.endswith("\n\nThe emergency stop is not an isolator.")
    # The span still describes text alone. That is the whole point of the field.
    assert chunk.end - chunk.start == len(chunk.text)


def test_content_hash_covers_embed_text_not_text() -> None:
    """ADR 0006's sharpest decision, asserted.

    Strategies 2 and 3 produce the same slice of the same document with a
    different header. Hashing text would give them one embedding cache key
    between them, and strategy 3 would be scored on strategy 2's vectors with
    every resulting number looking entirely plausible.
    """
    structural = a_chunk()
    contextual = a_chunk(strategy=StrategyId.CONTEXTUAL, context_prefix="Manual > Safety")

    assert structural.text == contextual.text
    assert structural.content_hash != contextual.content_hash
    assert (
        contextual.content_hash == hashlib.sha256(contextual.embed_text.encode("utf-8")).hexdigest()
    )


def test_chunk_id_distinguishes_the_strategies() -> None:
    """Three strategies chunk the same document and share an embedding cache.

    Without the strategy in the id, two of their chunks collide on the same key
    and whichever was written last wins.
    """
    assert a_chunk().chunk_id == "sdm-43-service-manual:structural:14"
    assert a_chunk(strategy=StrategyId.FIXED).chunk_id == "sdm-43-service-manual:fixed:14"


def test_a_blank_prefix_is_rejected() -> None:
    """A blank prefix is not the same as no prefix, and reads as one.

    It produces embed_text with two leading newlines, embedding one chunk
    slightly differently from its unprefixed neighbours -- exactly the silent
    skew an A/B between strategies cannot survive.
    """
    with pytest.raises(ValidationError):
        a_chunk(context_prefix="  ")


def test_a_contextual_chunk_must_carry_a_prefix() -> None:
    """The contextual strategy is defined by the header. One without it is
    indistinguishable from a structural chunk and would silently duplicate it."""
    with pytest.raises(ValidationError, match="context_prefix"):
        a_chunk(strategy=StrategyId.CONTEXTUAL)


def test_a_non_contextual_chunk_must_not_carry_a_prefix() -> None:
    with pytest.raises(ValidationError, match="context_prefix"):
        a_chunk(strategy=StrategyId.FIXED, context_prefix="Manual > Safety")
```

Add `import hashlib`, `from datetime import date`, `from pydantic import ValidationError` and `StrategyId` to the module's imports.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_models.py -v`
Expected: FAIL — `extra fields not permitted` on the new keys.

- [ ] **Step 3: Implement**

Replace `Chunk` in `src/fleet_copilot/ingest/models.py`:

```python
class StrategyId(StrEnum):
    """Which chunking strategy produced a chunk.

    Part of chunk_id rather than only of the surrounding run: all three
    strategies chunk the same documents and share one embedding cache, so two
    of their chunks would otherwise collide on a key and the last write wins.
    """

    FIXED = "fixed"
    STRUCTURAL = "structural"
    CONTEXTUAL = "contextual"


class Chunk(BaseModel):
    """A contiguous slice of a :class:`Document`; the unit the retriever indexes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: NonEmptyStr
    type: DocumentType
    machine_types: tuple[str, ...] = ()
    item_numbers: tuple[str, ...] = ()
    language: Language
    revision: Annotated[int, Field(ge=1)]
    effective_date: date

    section_path: tuple[str, ...] = ()
    """Headings above this chunk, outermost first, with their '#' markers removed.

    Empty for the fixed-size baseline, which does not read headings -- that
    difference is one of the things the comparison is measuring.
    """

    chunk_index: Annotated[int, Field(ge=0)]
    strategy: StrategyId

    text: NonEmptyStr
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    context_prefix: NonEmptyStr | None = None
    """The contextual header, outside the span on purpose.

    A chunk's subject often appears only in the heading above it, which is not
    in its own text; embedding the breadcrumb recovers that without moving
    start/end, so a citation still highlights the source exactly.
    """

    @field_validator("context_prefix")
    @classmethod
    def _reject_a_blank_prefix(cls, value: str | None) -> str | None:
        """Treat a whitespace-only prefix as the error it is, not as no prefix.

        A blank prefix produces embed_text with two leading newlines, embedding
        one chunk slightly differently from its unprefixed neighbours -- the kind
        of skew that makes a strategy A/B measure the wrong thing.
        """
        if value is not None and not value.strip():
            msg = "context_prefix must not be blank; omit it instead"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _check_span_matches_text(self) -> Self:
        """Keep the span honest so citations can point back into the source.

        A chunk whose offsets disagree with its own text will highlight the
        wrong passage in the UI, and the error is invisible until a human reads
        the citation -- so it is rejected at construction time.
        """
        if self.end <= self.start:
            msg = f"end ({self.end}) must be greater than start ({self.start})"
            raise ValueError(msg)
        if self.end - self.start != len(self.text):
            msg = (
                f"span {self.start}:{self.end} covers {self.end - self.start} characters "
                f"but text is {len(self.text)} characters long"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _prefix_belongs_to_the_contextual_strategy_alone(self) -> Self:
        """Tie the header to the strategy named after it.

        A contextual chunk without a prefix is byte-identical to its structural
        twin and would duplicate it in the index under a different id; a fixed
        chunk with one would quietly stop being a baseline.
        """
        wants_prefix = self.strategy is StrategyId.CONTEXTUAL
        if wants_prefix and self.context_prefix is None:
            msg = "the contextual strategy requires a context_prefix"
            raise ValueError(msg)
        if not wants_prefix and self.context_prefix is not None:
            msg = f"the {self.strategy.value} strategy must not carry a context_prefix"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def chunk_id(self) -> str:
        """Identifier that is stable across re-ingests of the same document."""
        return f"{self.doc_id}:{self.strategy.value}:{self.chunk_index}"

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def embed_text(self) -> str:
        """What the retriever embeds, as opposed to what a citation quotes."""
        if self.context_prefix is None:
            return self.text
        return f"{self.context_prefix}\n\n{self.text}"

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def content_hash(self) -> str:
        """SHA-256 of what is actually embedded. The embedding cache key.

        Deliberately over embed_text and not over text: see ADR 0006. Two
        strategies can produce the same slice with different headers, and
        hashing text would hand them one cached vector between them.
        """
        return hashlib.sha256(self.embed_text.encode("utf-8")).hexdigest()
```

Imports: `hashlib`, `Annotated`, `Self`, `computed_field`, `field_validator` and `model_validator` are already in this module. Widen the existing `from datetime import UTC, datetime` to `from datetime import UTC, date, datetime`, and add `from enum import StrEnum` and `from fleet_copilot.corpus.models import DocumentType, Language`.

`Document` is unchanged. This task replaces `Chunk` only.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_models.py -v && uv run mypy`
Expected: all pass, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/models.py tests/ingest/test_models.py
git commit -m "feat(ingest): give Chunk the contract three strategies share

The ten fields ADR 0006 requires, plus the strategy that produced it. Without
the strategy in chunk_id, two strategies' chunks collide on one key in an
embedding cache they share, and the last write wins.

content_hash covers embed_text rather than text. Strategies 2 and 3 produce the
same slice with different headers; hashing text would give them one cached
vector between them and score the contextual strategy on the structural one's
embeddings, with every resulting number looking plausible.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Counting tokens without reaching the network

**Files:**
- Create: `src/fleet_copilot/ingest/chunking/__init__.py`, `src/fleet_copilot/ingest/chunking/tokens.py`
- Create: `tests/ingest/chunking/__init__.py`, `tests/ingest/chunking/test_tokens.py`, `tests/ingest/chunking/fixtures/token_counts.json`
- Create: `scripts/capture_token_counts.py`

**Interfaces:**
- Consumes: `Language` from `fleet_copilot.corpus.models`.
- Produces: `class TokenCounter(Protocol)` with `count(text: str, language: Language) -> int`; `class HeuristicCounter`; `class TiktokenCounter`; `CHARS_PER_TOKEN: Mapping[Language, float]`.

- [ ] **Step 1: Capture the fixture**

Write `scripts/capture_token_counts.py`:

```python
"""Capture exact cl100k_base token counts to test the heuristic against.

Run deliberately, on a machine with network access:

    uv run --with tiktoken python scripts/capture_token_counts.py

tiktoken downloads its BPE table over HTTPS on first use, which is why it is
never on a path `just check` executes. The counts it produces are committed so
the heuristic can be held to them offline, forever.
"""

from __future__ import annotations

import json
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus" / "markdown"
TARGET = ROOT / "tests" / "ingest" / "chunking" / "fixtures" / "token_counts.json"


def main() -> None:
    encoding = tiktoken.get_encoding("cl100k_base")
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    samples = []
    for path in sorted(CORPUS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[2].lstrip("\n") if text.startswith("---") else text
        samples.append(
            {
                "doc_id": path.stem,
                "language": "hu" if path.stem.endswith("-hu") else "en",
                "characters": len(body),
                "tokens": len(encoding.encode(body)),
            }
        )

    payload = {"encoding": "cl100k_base", "samples": samples}
    TARGET.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{TARGET.name}: {len(samples)} samples")


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `uv run --with tiktoken python scripts/capture_token_counts.py`
Expected: `token_counts.json: 120 samples`

- [ ] **Step 2: Write the failing tests**

Create `tests/ingest/chunking/test_tokens.py`:

```python
"""Token budgets, and how close the offline heuristic gets to the real thing."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.corpus.models import Language
from fleet_copilot.ingest.chunking.tokens import CHARS_PER_TOKEN, HeuristicCounter, TokenCounter

FIXTURE = Path(__file__).parent / "fixtures" / "token_counts.json"


def samples() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["encoding"] == "cl100k_base"
    rows: list[dict[str, Any]] = payload["samples"]
    return rows


def test_hungarian_needs_its_own_ratio() -> None:
    """Measured, not assumed: Hungarian costs roughly twice the tokens.

    cl100k_base was not built for an agglutinative language, so the same
    character budget buys 1.9x the tokens. One global ratio would make every
    Hungarian chunk nearly twice the size the chunker believed it was.
    """
    assert CHARS_PER_TOKEN[Language.EN] > 4.0
    assert CHARS_PER_TOKEN[Language.HU] < 2.5


def test_the_heuristic_tracks_the_real_tokenizer_per_document() -> None:
    """Held to 15%. Boundaries only have to be stable and roughly right.

    A chunker that misjudges a budget by a tenth produces slightly uneven
    chunks; one that misjudges it by half produces chunks that will not embed.
    """
    counter = HeuristicCounter()

    errors = []
    for row in samples():
        language = Language(row["language"])
        estimated = counter.count("x" * int(row["characters"]), language)
        errors.append(abs(estimated - row["tokens"]) / row["tokens"])

    assert statistics.median(errors) < 0.15
    assert max(errors) < 0.40


def test_an_empty_string_costs_nothing() -> None:
    assert HeuristicCounter().count("", Language.EN) == 0


def test_a_short_string_still_costs_at_least_one_token() -> None:
    """Rounding to zero would let a window accept text forever."""
    assert HeuristicCounter().count("a", Language.EN) == 1


def test_budget_to_characters_round_trips() -> None:
    counter = HeuristicCounter()

    characters = counter.characters_for(100, Language.EN)

    assert counter.count("x" * characters, Language.EN) == pytest.approx(100, abs=1)


def test_heuristic_counter_satisfies_the_protocol() -> None:
    assert isinstance(HeuristicCounter(), TokenCounter)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.chunking'`

- [ ] **Step 4: Implement `src/fleet_copilot/ingest/chunking/tokens.py`**

```python
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

Regenerate with `just chunk-stats --calibrate` after any corpus change.
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
```

Create `src/fleet_copilot/ingest/chunking/__init__.py`:

```python
"""Split a parsed document into the units the retriever indexes."""
```

- [ ] **Step 5: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/test_tokens.py -v`
Expected: 6 passed.

If `test_the_heuristic_tracks_the_real_tokenizer_per_document` fails, re-derive the ratios from the fixture rather than loosening the tolerance — the fixture is the ground truth and the constants are what must move.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/ tests/ingest/chunking/ scripts/capture_token_counts.py
git commit -m "feat(chunking): count tokens offline, with a ratio per language

tiktoken downloads its BPE table over HTTPS on first use, so it cannot decide a
chunk boundary: a boundary that depends on whether a download succeeded is not
a boundary. The default is a measured ratio, held to within 15% of the real
tokenizer by a committed fixture.

The ratio is per language because the measurement demanded it -- 4.17
characters per token in English against 2.21 in Hungarian. One global ratio
would let every Hungarian chunk run to nearly twice its intended budget.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Document context and the section path

**Files:**
- Create: `src/fleet_copilot/ingest/chunking/context.py`
- Test: `tests/ingest/chunking/test_context.py`

**Interfaces:**
- Consumes: `ManifestEntry`, `ParsedDocument`, `ParsedBlock`, `BlockRole`.
- Produces: `class DocumentContext` (frozen) built by `DocumentContext.from_entry(entry)`, with `chunk_fields()` returning the metadata every `Chunk` carries; `heading_text(block) -> str`; `section_path_at(document, offset) -> tuple[str, ...]`; `contextual_header(context, section_path) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/chunking/test_context.py`:

```python
"""Where a chunk's metadata and its breadcrumb come from."""

from __future__ import annotations

from datetime import date

from fleet_copilot.corpus.manifest import ManifestEntry
from fleet_copilot.ingest.chunking.context import (
    DocumentContext,
    contextual_header,
    heading_text,
    section_path_at,
)
from fleet_copilot.ingest.parse import BlockRole, ParsedBlock, ParsedDocument, ParsedPage, ParserId

CONTENT = (
    "# Single-disc machine SDM-43 - service manual\n"
    "\n"
    "## Scope\n"
    "\n"
    "For service technicians.\n"
    "\n"
    "## Safety\n"
    "\n"
    "The emergency stop is not an isolator.\n"
)


def an_entry() -> ManifestEntry:
    return ManifestEntry.model_validate(
        {
            "doc_id": "sdm-43-service-manual",
            "type": "service_manual",
            "language": "en",
            "format": "pdf",
            "path": "published/sdm-43-service-manual.pdf",
            "sha256": "0" * 64,
            "bytes": 2639,
            "machine_types": ["SDM-43"],
            "item_numbers": ["1.291-101.0"],
            "revision": 3,
            "effective_date": "2026-03-09",
        }
    )


def a_document() -> ParsedDocument:
    def block(role: BlockRole, needle: str) -> ParsedBlock:
        start = CONTENT.index(needle)
        return ParsedBlock(
            role=role, text=needle, start=start, end=start + len(needle), page_number=1
        )

    return ParsedDocument(
        doc_id="sdm-43-service-manual",
        source_sha256="0" * 64,
        parser=ParserId.MARKDOWN,
        model_id="markdown",
        content=CONTENT,
        blocks=(
            block(BlockRole.TITLE, "# Single-disc machine SDM-43 - service manual"),
            block(BlockRole.SECTION_HEADING, "## Scope"),
            block(BlockRole.PARAGRAPH, "For service technicians."),
            block(BlockRole.SECTION_HEADING, "## Safety"),
            block(BlockRole.PARAGRAPH, "The emergency stop is not an isolator."),
        ),
        pages=(ParsedPage(page_number=1, start=0, end=len(CONTENT)),),
    )


def test_heading_text_drops_the_markers() -> None:
    """Block text is a verbatim slice, so a heading arrives with its '#' intact.

    Left in, every breadcrumb would read '## Safety' and the hash markers would
    be embedded along with the words.
    """
    assert heading_text("## Safety") == "Safety"
    assert heading_text("# Single-disc machine SDM-43 - service manual") == (
        "Single-disc machine SDM-43 - service manual"
    )


def test_the_section_path_is_the_headings_above_an_offset() -> None:
    document = a_document()
    offset = CONTENT.index("The emergency stop")

    assert section_path_at(document, offset) == (
        "Single-disc machine SDM-43 - service manual",
        "Safety",
    )


def test_a_later_heading_replaces_an_earlier_one_at_the_same_level() -> None:
    """Scope and Safety are siblings; a chunk in Safety must not claim Scope."""
    document = a_document()

    assert section_path_at(document, CONTENT.index("For service")) == (
        "Single-disc machine SDM-43 - service manual",
        "Scope",
    )


def test_an_offset_before_any_heading_has_an_empty_path() -> None:
    assert section_path_at(a_document(), 0) == ()


def test_the_context_carries_every_metadata_field_a_chunk_needs() -> None:
    context = DocumentContext.from_entry(an_entry())

    fields = context.chunk_fields()
    assert fields["machine_types"] == ("SDM-43",)
    assert fields["item_numbers"] == ("1.291-101.0",)
    assert fields["revision"] == 3
    assert fields["effective_date"] == date(2026, 3, 9)


def test_the_contextual_header_names_the_machine_and_the_part() -> None:
    """ADR 0006 fixes the shape: title > section path | machines | item numbers.

    A query naming a machine type finds the chunk even when the prose does not
    repeat it, which is the whole reason strategy 3 exists.
    """
    context = DocumentContext.from_entry(an_entry())

    header = contextual_header(context, ("Single-disc machine SDM-43 - service manual", "Safety"))

    assert header == ("Single-disc machine SDM-43 - service manual > Safety | SDM-43 | 1.291-101.0")


def test_a_header_omits_empty_parts_rather_than_leaving_separators() -> None:
    """A handover note has no item numbers; a trailing ' | ' would be embedded."""
    entry = an_entry().model_copy(update={"item_numbers": ()})

    header = contextual_header(DocumentContext.from_entry(entry), ("Manual", "Safety"))

    assert header == "Manual > Safety | SDM-43"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.chunking.context'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/chunking/context.py`**

```python
"""The metadata a chunk carries, and the breadcrumb strategy 3 prepends.

Metadata comes from the manifest rather than from the parsed document: the PDF
and DOCX renderers drop the front matter, so for the 25 converted documents the
manifest is the only place machine types, revision and effective date survive
(ADR 0006).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from fleet_copilot.corpus.manifest import ManifestEntry
from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.ingest.parse import HEADING_ROLES, BlockRole, ParsedDocument

PATH_SEPARATOR: Final = " > "
FIELD_SEPARATOR: Final = " | "


def heading_text(text: str) -> str:
    """Strip the ATX markers from a heading block.

    Block text is a verbatim slice of content, so a heading arrives as '##
    Safety'. Left alone, every breadcrumb would carry hash marks into the
    embedding along with the words.
    """
    return text.lstrip("#").strip()


def _heading_level(text: str) -> int:
    """The ATX level of a heading block, or 1 for a title without markers."""
    stripped = len(text) - len(text.lstrip("#"))
    return max(1, stripped)


def section_path_at(document: ParsedDocument, offset: int) -> tuple[str, ...]:
    """The headings in force at ``offset``, outermost first.

    Levels replace rather than accumulate: two sibling sections at the same
    depth are alternatives, and a chunk inside the second one that still claimed
    the first would be retrieved for queries about a section it is not in.
    """
    path: dict[int, str] = {}
    for block in document.blocks:
        if block.start >= offset:
            break
        if block.role not in HEADING_ROLES:
            continue
        level = 1 if block.role is BlockRole.TITLE else _heading_level(block.text)
        path[level] = heading_text(block.text)
        for deeper in [key for key in path if key > level]:
            del path[deeper]
    return tuple(path[level] for level in sorted(path))


class DocumentContext(BaseModel):
    """Everything a chunk inherits from its document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    type: DocumentType
    language: Language
    machine_types: tuple[str, ...]
    item_numbers: tuple[str, ...]
    revision: int
    effective_date: date

    @classmethod
    def from_entry(cls, entry: ManifestEntry) -> DocumentContext:
        """Build the context from a manifest entry."""
        return cls(
            doc_id=entry.doc_id,
            type=entry.type,
            language=entry.language,
            machine_types=entry.machine_types,
            item_numbers=entry.item_numbers,
            revision=entry.revision,
            effective_date=entry.effective_date,
        )

    def chunk_fields(self) -> dict[str, Any]:
        """The metadata keys every Chunk carries, ready to splat into one."""
        return {
            "doc_id": self.doc_id,
            "type": self.type,
            "language": self.language,
            "machine_types": self.machine_types,
            "item_numbers": self.item_numbers,
            "revision": self.revision,
            "effective_date": self.effective_date,
        }


def contextual_header(context: DocumentContext, section_path: tuple[str, ...]) -> str:
    """Build the header strategy 3 prepends before embedding.

    ``<section path> | <machine types> | <item numbers>``, with empty parts
    dropped rather than left as dangling separators -- a handover note has no
    item numbers, and a trailing ' | ' would be embedded along with everything
    else.
    """
    parts = [
        PATH_SEPARATOR.join(section_path),
        ", ".join(context.machine_types),
        ", ".join(context.item_numbers),
    ]
    return FIELD_SEPARATOR.join(part for part in parts if part)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/test_context.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/context.py tests/ingest/chunking/test_context.py
git commit -m "feat(chunking): derive chunk metadata and the section breadcrumb

Metadata comes from the manifest, not from the parsed document: rendering drops
the front matter, so for the 25 converted documents the manifest is the only
place machine types, revision and effective date survive.

Heading levels replace rather than accumulate. Two sibling sections are
alternatives, and a chunk in the second that still claimed the first would be
retrieved for queries about a section it is not in.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The structural chunker

Heading-aware. Tables are their own chunks; ordered step lists are never split.

**Files:**
- Create: `src/fleet_copilot/ingest/chunking/base.py`, `src/fleet_copilot/ingest/chunking/structural.py`
- Test: `tests/ingest/chunking/test_structural.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `class Chunker(Protocol)` with `chunk(document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]`; `class StructuralChunker(target_tokens: int = 220, counter: TokenCounter | None = None)`; `is_step_line(line: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/chunking/test_structural.py`:

```python
"""Heading-aware chunking, and the two things it refuses to break."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.build import manifest_path
from fleet_copilot.ingest.chunking.context import DocumentContext
from fleet_copilot.ingest.chunking.structural import StructuralChunker, is_step_line
from fleet_copilot.ingest.markdown import MarkdownParser
from fleet_copilot.ingest.models import StrategyId
from fleet_copilot.ingest.parse import ParsedDocument

CORPUS = Path(__file__).resolve().parents[3] / "data" / "corpus" / "markdown"
MANUAL = CORPUS / "sdm-43-service-manual.md"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"


async def the_manual() -> tuple[ParsedDocument, DocumentContext]:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )
    manifest = load_manifest(manifest_path(None))
    entry = next(e for e in manifest.documents if e.doc_id == "sdm-43-service-manual")
    return document, DocumentContext.from_entry(entry)


def test_step_lines_are_recognised_and_bullets_are_not() -> None:
    """A procedure is a sequence; half of one reads as a whole one and is
    dangerous. A bullet list is a set of independent statements, so splitting it
    costs nothing but a little context."""
    assert is_step_line("1. Verify the float shut-off operates freely.")
    assert is_step_line("  10. Measure at the connector.")
    assert not is_step_line("- Order consumables against the item number.")
    assert not is_step_line("Ordinary prose.")


@pytest.mark.asyncio
async def test_every_chunk_is_a_verbatim_slice_of_content() -> None:
    document, context = await the_manual()

    for chunk in StructuralChunker().chunk(document, context):
        assert document.content[chunk.start : chunk.end] == chunk.text


@pytest.mark.asyncio
async def test_chunk_indices_are_contiguous_and_start_at_zero() -> None:
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.strategy is StrategyId.STRUCTURAL for chunk in chunks)


@pytest.mark.asyncio
async def test_a_table_is_its_own_chunk_and_holds_its_header_row() -> None:
    """ADR 0006. An interval table answers a different question from the
    paragraph above it, and a row separated from its column headers is noise."""
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)
    table_chunks = [chunk for chunk in chunks if chunk.text.lstrip().startswith("|")]

    assert len(table_chunks) == 1
    table = table_chunks[0]
    assert "| Interval | Task |" in table.text
    assert "1000 h" in table.text
    assert "For service technicians" not in table.text


@pytest.mark.asyncio
async def test_a_step_list_is_never_split() -> None:
    """The diagnostics list has six steps. They stay in one chunk even when that
    chunk overruns the target, because half a procedure reads as a whole one."""
    document, context = await the_manual()

    chunks = StructuralChunker(target_tokens=40).chunk(document, context)

    holding = [chunk for chunk in chunks if "1. Verify the float shut-off" in chunk.text]
    assert len(holding) == 1
    assert "6. Check the battery state of charge" in holding[0].text


@pytest.mark.asyncio
async def test_a_chunk_carries_the_section_it_came_from() -> None:
    document, context = await the_manual()

    chunks = StructuralChunker().chunk(document, context)

    safety = next(chunk for chunk in chunks if "emergency stop is not an isolator" in chunk.text)
    assert safety.section_path[-1] == "Safety"
    assert safety.machine_types == ("SDM-43",)
    assert safety.context_prefix is None, "the header belongs to the contextual strategy"


@pytest.mark.asyncio
async def test_a_document_always_produces_at_least_one_chunk() -> None:
    document, context = await the_manual()

    assert StructuralChunker().chunk(document, context)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_structural.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.chunking.structural'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/chunking/base.py`**

```python
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
```

Implement `src/fleet_copilot/ingest/chunking/structural.py`:

```python
"""Chunk on the structure the parser recovered.

Two rules override the size target, both from ADR 0006. A table is its own
chunk, because an interval table answers a different question from the prose
around it and a row separated from its column headers is noise. An ordered step
list is never split, because half a procedure reads exactly like a whole one.

Both rules make the size distribution bimodal on purpose. A stats table that
shows otherwise means they are not being applied.
"""

from __future__ import annotations

import re
from typing import Final

from fleet_copilot.corpus.models import Language
from fleet_copilot.ingest.chunking.context import DocumentContext, section_path_at
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk, StrategyId
from fleet_copilot.ingest.parse import HEADING_ROLES, BlockRole, ParsedBlock, ParsedDocument

DEFAULT_TARGET_TOKENS: Final = 220
"""Chosen against the corpus, not from convention.

The median document is 184 tokens, so the usual 512 would leave 108 of 120
documents as a single chunk and the three-way comparison would be measuring
nothing. 220 sits just above the median document and just below the p75, which
splits the long documents and leaves the short ones whole.
"""

STEP_LINE = re.compile(r"^\s*\d+\.\s")
"""An ordered-list line. Bullets are deliberately not matched: a bullet list is
a set of independent statements and splitting it costs a little context, while
a procedure is a sequence and half of one is actively dangerous."""

SKIPPED_ROLES: Final = frozenset(
    {BlockRole.PAGE_HEADER, BlockRole.PAGE_FOOTER, BlockRole.PAGE_NUMBER}
)
"""Page furniture. Kept in content by the parser so offsets stay stable, skipped
here because a chunk of a page number retrieves nothing and costs an embedding."""


def is_step_line(line: str) -> bool:
    """Whether ``line`` is a numbered step."""
    return bool(STEP_LINE.match(line))


def _has_steps(text: str) -> bool:
    return any(is_step_line(line) for line in text.splitlines())


class StructuralChunker:
    """Packs blocks under their heading, up to a token target."""

    def __init__(
        self, target_tokens: int = DEFAULT_TARGET_TOKENS, counter: TokenCounter | None = None
    ) -> None:
        self._target = target_tokens
        self._counter = HeuristicCounter() if counter is None else counter

    @property
    def strategy(self) -> str:
        return StrategyId.STRUCTURAL.value

    def _emit(
        self,
        document: ParsedDocument,
        context: DocumentContext,
        start: int,
        end: int,
        index: int,
    ) -> Chunk:
        return Chunk(
            **context.chunk_fields(),
            section_path=section_path_at(document, start),
            chunk_index=index,
            strategy=StrategyId.STRUCTURAL,
            text=document.content[start:end],
            start=start,
            end=end,
        )

    def _is_atomic(self, block: ParsedBlock) -> bool:
        """Whether ``block`` must be a chunk on its own, whatever its size."""
        return block.role is BlockRole.TABLE or _has_steps(block.text)

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]:
        """Split ``document``. Never returns empty for a non-empty document."""
        language: Language = context.language
        chunks: list[Chunk] = []
        run_start: int | None = None
        run_end = 0
        run_tokens = 0

        def flush() -> None:
            nonlocal run_start, run_tokens
            if run_start is not None:
                chunks.append(self._emit(document, context, run_start, run_end, len(chunks)))
                run_start = None
                run_tokens = 0

        for block in document.blocks:
            if block.role in SKIPPED_ROLES:
                continue

            # A heading opens a new chunk rather than joining the one before it:
            # it describes what follows, and trailing it onto the previous
            # section puts it in the wrong breadcrumb.
            if block.role in HEADING_ROLES or self._is_atomic(block):
                flush()

            cost = self._counter.count(block.text, language)
            if run_start is not None and run_tokens + cost > self._target:
                flush()

            if run_start is None:
                run_start = block.start
            run_end = block.end
            run_tokens += cost

            if self._is_atomic(block):
                flush()

        flush()
        return tuple(chunks)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/test_structural.py -v`
Expected: 7 passed.

If `test_a_table_is_its_own_chunk_and_holds_its_header_row` fails with prose in the table chunk, `_is_atomic` is running after the block has already joined a run — the `flush()` before the size check is what keeps a table from inheriting its predecessor.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/base.py \
        src/fleet_copilot/ingest/chunking/structural.py \
        tests/ingest/chunking/test_structural.py
git commit -m "feat(chunking): add the heading-aware strategy

Tables become their own chunks and ordered step lists are never split, both
overriding the size target. A table row separated from its column headers is
noise, and half a procedure reads exactly like a whole one.

The target is 220 tokens, chosen against this corpus rather than from
convention: the median document is 184 tokens, so the usual 512 would leave 108
of 120 documents as a single chunk and the three-way comparison would measure
nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The fixed-size baseline

**Files:**
- Create: `src/fleet_copilot/ingest/chunking/fixed.py`
- Test: `tests/ingest/chunking/test_fixed.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `class FixedWindowChunker(target_tokens: int = 220, overlap_tokens: int = 40, counter: TokenCounter | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/chunking/test_fixed.py`:

```python
"""The baseline: no structure, one window, sliding."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_every_chunk_is_a_verbatim_slice_of_content() -> None:
    document, context = await the_manual()

    for chunk in FixedWindowChunker().chunk(document, context):
        assert document.content[chunk.start : chunk.end] == chunk.text


@pytest.mark.asyncio
async def test_the_windows_cover_the_whole_document() -> None:
    """A baseline that dropped text would flatter itself on precision."""
    document, context = await the_manual()

    chunks = FixedWindowChunker().chunk(document, context)

    assert chunks[0].start == 0
    assert chunks[-1].end == len(document.content)
    for earlier, later in zip(chunks, chunks[1:], strict=True):
        assert later.start <= earlier.end, "a gap between windows loses text"


@pytest.mark.asyncio
async def test_consecutive_windows_overlap() -> None:
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=60, overlap_tokens=15).chunk(document, context)

    assert len(chunks) > 1
    for earlier, later in zip(chunks, chunks[1:], strict=True):
        assert later.start < earlier.end, "no overlap means a sentence can fall between windows"


@pytest.mark.asyncio
async def test_it_carries_no_section_path_and_no_prefix() -> None:
    """The baseline does not read headings. That difference is the measurement."""
    document, context = await the_manual()

    for chunk in FixedWindowChunker().chunk(document, context):
        assert chunk.section_path == ()
        assert chunk.context_prefix is None
        assert chunk.strategy is StrategyId.FIXED


@pytest.mark.asyncio
async def test_it_does_split_a_step_list() -> None:
    """Not a defect. Story 3.3 needs to attribute a retrieval failure to exactly
    this, so the baseline has to actually commit the error."""
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=40, overlap_tokens=8).chunk(document, context)
    holding = [chunk for chunk in chunks if "1. Verify the float shut-off" in chunk.text]

    assert holding, "the diagnostics list is in the document"
    assert "6. Check the battery state of charge" not in holding[0].text


@pytest.mark.asyncio
async def test_windows_do_not_cut_mid_word() -> None:
    """The one concession, so the baseline is fair rather than a straw man.

    A window that ends 'the squee' embeds a token sequence no query produces,
    and the comparison would be measuring tokenisation damage instead of
    boundary placement.
    """
    document, context = await the_manual()

    for chunk in FixedWindowChunker(target_tokens=50).chunk(document, context):
        if chunk.end < len(document.content):
            assert (
                document.content[chunk.end - 1].isspace() or document.content[chunk.end].isspace()
            )


@pytest.mark.asyncio
async def test_a_document_shorter_than_the_window_is_one_chunk() -> None:
    document, context = await the_manual()

    chunks = FixedWindowChunker(target_tokens=100_000).chunk(document, context)

    assert len(chunks) == 1
    assert chunks[0].text == document.content
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_fixed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.chunking.fixed'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/chunking/fixed.py`**

```python
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/test_fixed.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/fixed.py tests/ingest/chunking/test_fixed.py
git commit -m "feat(chunking): add the fixed-window baseline

Deliberately blind to structure: it splits tables and step lists and carries no
section path, because story 3.3 has to attribute a retrieval failure to exactly
those. A baseline that quietly respected structure would make the comparison
flattering and useless.

Its window is the same 220 tokens the structural chunker targets. Different
sizes would confound size with boundary placement and the result would say
nothing about structure.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The contextual chunker

**Files:**
- Modify: `src/fleet_copilot/ingest/chunking/structural.py`, `src/fleet_copilot/ingest/chunking/__init__.py`
- Test: `tests/ingest/chunking/test_contextual.py`

**Interfaces:**
- Consumes: Task 4's `StructuralChunker`, Task 3's `contextual_header`.
- Produces: `class ContextualChunker(structural: StructuralChunker | None = None)`; `CHUNKERS: Mapping[StrategyId, Chunker]` in `base.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/chunking/test_contextual.py`:

```python
"""Strategy 3 differs from strategy 2 by one field, and this proves it."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.chunking.structural import ContextualChunker, StructuralChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_the_boundaries_are_identical_to_the_structural_ones() -> None:
    """The whole point of the A/B.

    If the two strategies cut in different places, story 3.3 cannot tell whether
    a difference came from the header or from the boundaries, and the experiment
    has two variables.
    """
    document, context = await the_manual()

    structural = StructuralChunker().chunk(document, context)
    contextual = ContextualChunker().chunk(document, context)

    assert [(c.start, c.end) for c in structural] == [(c.start, c.end) for c in contextual]
    assert [c.text for c in structural] == [c.text for c in contextual]


@pytest.mark.asyncio
async def test_every_chunk_carries_a_header() -> None:
    document, context = await the_manual()

    for chunk in ContextualChunker().chunk(document, context):
        assert chunk.context_prefix
        assert chunk.strategy is StrategyId.CONTEXTUAL


@pytest.mark.asyncio
async def test_the_header_puts_the_machine_type_in_reach_of_a_query() -> None:
    """The reason strategy 3 exists: the safety bullet never names the machine."""
    document, context = await the_manual()

    safety = next(
        chunk
        for chunk in ContextualChunker().chunk(document, context)
        if "emergency stop is not an isolator" in chunk.text
    )

    assert "SDM-43" not in safety.text
    assert "SDM-43" in safety.embed_text
    assert "Safety" in safety.embed_text


@pytest.mark.asyncio
async def test_the_content_hashes_differ_from_the_structural_ones() -> None:
    """ADR 0006's collision, checked end to end.

    Same slice, different header, so the embedding cache must see two keys. One
    key would score this strategy on the other's vectors.
    """
    document, context = await the_manual()

    structural = {c.content_hash for c in StructuralChunker().chunk(document, context)}
    contextual = {c.content_hash for c in ContextualChunker().chunk(document, context)}

    assert structural.isdisjoint(contextual)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_contextual.py -v`
Expected: FAIL — `ImportError: cannot import name 'ContextualChunker'`

- [ ] **Step 3: Implement**

Append to `src/fleet_copilot/ingest/chunking/structural.py`:

```python
class ContextualChunker:
    """The structural strategy plus a header. Implements Chunker.

    A wrapper rather than a copy, so the two strategies cannot drift apart. They
    must cut in exactly the same places: if they did not, story 3.3 could not
    tell whether a difference came from the header or from the boundaries, and
    the experiment would have two variables instead of one.
    """

    def __init__(self, structural: StructuralChunker | None = None) -> None:
        self._structural = StructuralChunker() if structural is None else structural

    @property
    def strategy(self) -> str:
        return StrategyId.CONTEXTUAL.value

    def chunk(self, document: ParsedDocument, context: DocumentContext) -> tuple[Chunk, ...]:
        """Re-emit the structural chunks, each carrying its breadcrumb."""
        return tuple(
            base.model_copy(
                update={
                    "strategy": StrategyId.CONTEXTUAL,
                    "context_prefix": contextual_header(context, base.section_path)
                    or context.doc_id,
                }
            )
            for base in self._structural.chunk(document, context)
        )
```

Add `contextual_header` to the module's imports from `.context`.

The `or context.doc_id` fallback covers a chunk before the first heading in a document with no machine types: `contextual_header` would return an empty string, and `Chunk` rejects a blank prefix. The doc_id is the weakest useful header rather than a crash.

Add to `src/fleet_copilot/ingest/chunking/base.py`:

```python
def chunkers() -> Mapping[StrategyId, Chunker]:
    """One instance per strategy, with the defaults the stats table was built on.

    A function rather than a module-level dict so the imports stay one-way:
    base defines the Protocol, and the strategies import it.
    """
    from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
    from fleet_copilot.ingest.chunking.structural import ContextualChunker, StructuralChunker

    return {
        StrategyId.FIXED: FixedWindowChunker(),
        StrategyId.STRUCTURAL: StructuralChunker(),
        StrategyId.CONTEXTUAL: ContextualChunker(),
    }
```

Fill `src/fleet_copilot/ingest/chunking/__init__.py`:

```python
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/ -v && uv run mypy`
Expected: all pass, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/ tests/ingest/chunking/test_contextual.py
git commit -m "feat(chunking): add the contextual strategy as a wrapper, not a copy

Strategies 2 and 3 must cut in exactly the same places. Implemented as a wrapper
so they cannot drift: if the boundaries differed, story 3.3 could not tell
whether a result came from the header or from the boundaries, and the experiment
would have two variables.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Chunk the whole corpus

**Files:**
- Modify: `src/fleet_copilot/ingest/run.py`
- Test: `tests/ingest/test_run.py`

**Interfaces:**
- Consumes: Plan 1's `run.py`, Tasks 1–6.
- Produces: `async def parse_all(manifest, corpus_root, settings) -> AsyncIterator[tuple[ParsedDocument, DocumentContext]]`; `async def chunk_all(...) -> dict[StrategyId, list[Chunk]]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_run.py`:

```python
@pytest.mark.asyncio
async def test_every_strategy_chunks_every_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """The story's first acceptance criterion, over the Markdown corpus.

    The 25 converted documents need the layout cache, so this covers the 95 that
    parse offline -- enough to prove no strategy drops a document, which is the
    failure a stats table would hide behind an average.
    """
    from fleet_copilot.ingest.run import chunk_markdown_corpus

    manifest = load_manifest(manifest_path(None))

    by_strategy = await chunk_markdown_corpus(manifest, corpus_root(None))

    assert set(by_strategy) == set(StrategyId)
    for strategy, chunks in by_strategy.items():
        covered = {chunk.doc_id for chunk in chunks}
        assert len(covered) == 95, f"{strategy.value} lost a document"
        assert all(chunk.text.strip() for chunk in chunks)
```

Add `StrategyId` to the module's imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/ingest/test_run.py -v -k every_strategy`
Expected: FAIL — `ImportError: cannot import name 'chunk_markdown_corpus'`

- [ ] **Step 3: Implement**

Append to `src/fleet_copilot/ingest/run.py`:

```python
async def chunk_markdown_corpus(
    manifest: Manifest, corpus_root: Path
) -> dict[StrategyId, list[Chunk]]:
    """Chunk every Markdown document under all three strategies.

    Needs no Azure account, which is why it is what the test suite exercises:
    the 95 Markdown documents are 79% of the corpus and cover both languages,
    every document type and every structure the chunkers care about.
    """
    parser = MarkdownParser()
    strategies = chunkers()
    by_strategy: dict[StrategyId, list[Chunk]] = {key: [] for key in strategies}

    entries = {entry.doc_id: entry for entry in manifest.documents}
    for target in native_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        document = await parser.parse(data, doc_id=target.doc_id, content_type=target.content_type)
        context = DocumentContext.from_entry(entries[target.doc_id])
        for key, chunker in strategies.items():
            by_strategy[key].extend(chunker.chunk(document, context))

    return by_strategy


async def chunk_corpus(
    manifest: Manifest, corpus_root: Path, settings: Settings
) -> dict[StrategyId, list[Chunk]]:
    """Chunk all 120 documents. The converted 25 come from the layout cache.

    Raises if a converted document is not cached rather than analysing it:
    chunking is not the place to spend money, and an uncached document means
    `just corpus-parse --apply` has not been run.
    """
    by_strategy = await chunk_markdown_corpus(manifest, corpus_root)

    di_endpoint, blob_endpoint, container = endpoints(settings)
    api_version = settings.azure_document_intelligence_api_version
    cache = BlobLayoutCache(blob_endpoint, container)
    parser = CachedParser(
        AzureLayoutParser(di_endpoint, api_version=api_version), cache, api_version=api_version
    )
    strategies = chunkers()
    entries = {entry.doc_id: entry for entry in manifest.documents}

    for target in analyse_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        key = parser.key_for(data)
        payload = await cache.get(key)
        if payload is None:
            msg = f"{target.doc_id} is not in the layout cache; run `just corpus-parse --apply`"
            raise CorpusDataError(msg)
        document = layout_from_analyze_result(
            payload,
            doc_id=target.doc_id,
            source_sha256=hashlib.sha256(data).hexdigest(),
        )
        context = DocumentContext.from_entry(entries[target.doc_id])
        for strategy_id, chunker in strategies.items():
            by_strategy[strategy_id].extend(chunker.chunk(document, context))

    return by_strategy
```

Add the imports this needs: `from fleet_copilot.ingest.chunking import DocumentContext`, `from fleet_copilot.ingest.chunking.base import chunkers`, `from fleet_copilot.ingest.models import Chunk, StrategyId`.

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/ingest/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/run.py tests/ingest/test_run.py
git commit -m "feat(ingest): chunk the corpus under all three strategies

chunk_corpus refuses to analyse an uncached document rather than quietly
spending money: chunking is not where a bill should appear, and an uncached
document means the parse step has not been run.

The test exercises the 95 Markdown documents, which need no Azure account and
still cover both languages, every document type and every structure the
chunkers care about.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The stats table and `docs/chunking.md`

**Files:**
- Create: `src/fleet_copilot/ingest/chunking/stats.py`, `scripts/chunk_stats.py`, `docs/chunking.md`
- Modify: `justfile`, `README.md`
- Test: `tests/ingest/chunking/test_stats.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: `class StrategyStats` (frozen); `summarise(chunks, counter, ...) -> StrategyStats`; `as_markdown_table(stats) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/chunking/test_stats.py`:

```python
"""The numbers that go in docs/chunking.md."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.chunking.stats import as_markdown_table, summarise
from fleet_copilot.ingest.chunking.structural import StructuralChunker
from fleet_copilot.ingest.chunking.fixed import FixedWindowChunker
from fleet_copilot.ingest.models import StrategyId

from .test_structural import the_manual


@pytest.mark.asyncio
async def test_it_reports_a_distribution_not_just_a_mean() -> None:
    """A mean hides the bimodality the atomicity rules create on purpose."""
    document, context = await the_manual()
    chunks = StructuralChunker().chunk(document, context)

    stats = summarise(StrategyId.STRUCTURAL, chunks)

    assert stats.chunks == len(chunks)
    assert stats.documents == 1
    assert stats.min_tokens <= stats.median_tokens <= stats.max_tokens
    assert stats.p90_tokens >= stats.median_tokens


@pytest.mark.asyncio
async def test_it_counts_split_step_lists_because_that_is_the_headline() -> None:
    """The metric that shows the atomicity rule working, or not working.

    Structural must be zero. The baseline must not be, or it is not splitting
    anything and the comparison in 3.3 has no contrast to find.
    """
    document, context = await the_manual()

    structural = summarise(
        StrategyId.STRUCTURAL, StructuralChunker(target_tokens=40).chunk(document, context)
    )
    fixed = summarise(
        StrategyId.FIXED,
        FixedWindowChunker(target_tokens=40, overlap_tokens=8).chunk(document, context),
    )

    assert structural.split_step_lists == 0
    assert fixed.split_step_lists > 0


@pytest.mark.asyncio
async def test_the_table_renders_one_row_per_strategy() -> None:
    document, context = await the_manual()
    rows = [
        summarise(StrategyId.FIXED, FixedWindowChunker().chunk(document, context)),
        summarise(StrategyId.STRUCTURAL, StructuralChunker().chunk(document, context)),
    ]

    table = as_markdown_table(rows)

    assert table.startswith("| Strategy |")
    assert "| fixed |" in table
    assert "| structural |" in table
    assert table.endswith("\n")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/chunking/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.chunking.stats'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/chunking/stats.py`**

```python
"""Measure what each strategy produced.

A mean would hide the thing these strategies are built around: table and
step-list chunks are as long as they need to be, so the distribution is bimodal
by construction. Percentiles show that; an average conceals it.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from fleet_copilot.ingest.chunking.structural import is_step_line
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TokenCounter
from fleet_copilot.ingest.models import Chunk, StrategyId


def _counts_steps(text: str) -> int:
    return sum(1 for line in text.splitlines() if is_step_line(line))


class StrategyStats(BaseModel):
    """One row of the published table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: StrategyId
    documents: int
    chunks: int
    min_tokens: int
    median_tokens: int
    p90_tokens: int
    max_tokens: int
    chunks_per_document: float
    table_chunks: int
    split_step_lists: int


def summarise(
    strategy: StrategyId, chunks: Sequence[Chunk], counter: TokenCounter | None = None
) -> StrategyStats:
    """Reduce a strategy's chunks to the row that describes them.

    ``split_step_lists`` counts chunks that begin or end partway through an
    ordered list -- the metric that shows the atomicity rule either working or
    not. It must be zero for the structural strategies and non-zero for the
    baseline, or the comparison has no contrast to find.
    """
    counter = HeuristicCounter() if counter is None else counter
    if not chunks:
        return StrategyStats(
            strategy=strategy,
            documents=0,
            chunks=0,
            min_tokens=0,
            median_tokens=0,
            p90_tokens=0,
            max_tokens=0,
            chunks_per_document=0.0,
            table_chunks=0,
            split_step_lists=0,
        )

    sizes = sorted(counter.count(chunk.text, chunk.language) for chunk in chunks)
    documents = len({chunk.doc_id for chunk in chunks})

    split = 0
    for chunk in chunks:
        lines = chunk.text.splitlines()
        if not any(is_step_line(line) for line in lines):
            continue
        # A chunk holding steps that neither starts at step 1 nor ends at the
        # list's last step has cut into a procedure.
        first_step = next(line for line in lines if is_step_line(line))
        if not first_step.strip().startswith("1."):
            split += 1

    return StrategyStats(
        strategy=strategy,
        documents=documents,
        chunks=len(chunks),
        min_tokens=sizes[0],
        median_tokens=int(statistics.median(sizes)),
        p90_tokens=sizes[int(0.90 * (len(sizes) - 1))],
        max_tokens=sizes[-1],
        chunks_per_document=round(len(chunks) / documents, 2),
        table_chunks=sum(1 for chunk in chunks if chunk.text.lstrip().startswith("|")),
        split_step_lists=split,
    )


HEADER = (
    "| Strategy | Docs | Chunks | Chunks/doc | Min | Median | p90 | Max | "
    "Table chunks | Split step lists |"
)
DIVIDER = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"


def as_markdown_table(rows: Sequence[StrategyStats]) -> str:
    """Render the rows as the table docs/chunking.md publishes."""
    lines = [HEADER, DIVIDER]
    for row in rows:
        lines.append(
            f"| {row.strategy.value} | {row.documents} | {row.chunks} | "
            f"{row.chunks_per_document} | {row.min_tokens} | {row.median_tokens} | "
            f"{row.p90_tokens} | {row.max_tokens} | {row.table_chunks} | "
            f"{row.split_step_lists} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/chunking/test_stats.py -v`
Expected: 3 passed.

- [ ] **Step 5: Write `scripts/chunk_stats.py`**

```python
"""Report the chunk-size distribution per strategy.

    python scripts/chunk_stats.py                Markdown corpus only, offline
    python scripts/chunk_stats.py --all          all 120, reading the layout cache
    python scripts/chunk_stats.py --exact        report sizes with tiktoken
    python scripts/chunk_stats.py --calibrate    re-measure characters per token

Boundaries are always chosen by the offline heuristic so they are identical on
every machine. --exact only changes how the resulting chunks are *measured*.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest.chunking.stats import as_markdown_table, summarise
from fleet_copilot.ingest.chunking.tokens import HeuristicCounter, TiktokenCounter
from fleet_copilot.ingest.models import StrategyId
from fleet_copilot.ingest.run import chunk_corpus, chunk_markdown_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include the 25 converted documents")
    parser.add_argument("--exact", action="store_true", help="measure sizes with tiktoken")
    parser.add_argument("--calibrate", action="store_true", help="re-measure characters per token")
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(None))
        if args.all:
            settings = load_settings()
            by_strategy = asyncio.run(chunk_corpus(manifest, corpus_root(None), settings))
        else:
            by_strategy = asyncio.run(chunk_markdown_corpus(manifest, corpus_root(None)))
    except (CorpusDataError, SettingsError) as error:
        print(f"chunk-stats failed: {error}", file=sys.stderr)
        return 2

    counter = TiktokenCounter() if args.exact or args.calibrate else HeuristicCounter()
    rows = [summarise(strategy, by_strategy[strategy], counter) for strategy in StrategyId]

    print(f"counter: {'tiktoken cl100k_base' if args.exact or args.calibrate else 'heuristic'}")
    print()
    print(as_markdown_table(rows), end="")

    if args.calibrate:
        exact = TiktokenCounter()
        for language in {chunk.language for chunks in by_strategy.values() for chunk in chunks}:
            texts = [
                chunk.text
                for chunk in by_strategy[StrategyId.STRUCTURAL]
                if chunk.language is language
            ]
            characters = sum(len(text) for text in texts)
            tokens = sum(exact.count(text, language) for text in texts)
            print(f"chars per token, {language.value}: {characters / tokens:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Add the justfile recipe**

After `corpus-parse`:

```make
# Chunk-size distribution per strategy: `just chunk-stats --all` includes PDFs.
chunk-stats *args:
    uv run python scripts/chunk_stats.py {{args}}
```

- [ ] **Step 7: Produce the real numbers**

```bash
just chunk-stats
just chunk-stats --all
uv run --with tiktoken python scripts/chunk_stats.py --all --exact
```

Expected: three rows per run, and `split_step_lists` **0** for `structural` and `contextual`, non-zero for `fixed`. If structural is non-zero, Task 4's atomicity rule is not firing — fix the chunker, not the metric.

- [ ] **Step 8: Write `docs/chunking.md`**

Structure it as: what the three strategies are; the parameters and *why those numbers* (the median-document argument, the equal-size argument); the two atomicity rules; the measured characters-per-token table with the Hungarian finding; the generated stats table from Step 7, labelled with which counter measured it and the command that reproduces it; and what story 3.3 will compare. Paste the real output from Step 7 — do not retype it.

- [ ] **Step 9: Run the full gate**

Run: `just check`
Then: `uv run pre-commit run --all-files`
Expected: both clean. Paste the output; do not assert it passed.

- [ ] **Step 10: Commit**

```bash
git add src/fleet_copilot/ingest/chunking/stats.py scripts/chunk_stats.py \
        tests/ingest/chunking/test_stats.py justfile docs/chunking.md README.md
git commit -m "feat(chunking): publish the size distribution per strategy

Percentiles rather than a mean: table and step-list chunks are as long as they
need to be, so the distribution is bimodal by construction and an average would
conceal exactly the property the strategies are built around.

The headline column is split step lists -- zero for the structural strategies,
non-zero for the baseline. If it is ever zero for the baseline, the comparison
in 3.3 has no contrast to find.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Before Handing Back

1. `just check` — all three stages, output pasted, not asserted.
2. `uv run pre-commit run --all-files`.
3. `just chunk-stats --all` produces three rows covering **120 documents each** — the story's first acceptance criterion.
4. `docs/chunking.md` holds that table, and names the counter and the command that made it.
5. `split_step_lists` is 0 for `structural` and `contextual`.
6. The structural and contextual strategies produce identical `(start, end)` pairs, and disjoint `content_hash` sets.
7. Confirm the scope held: **no embedding call was made.** `content_hash` is computed, nothing is sent.

## What Plan 3 Inherits

- `chunk_corpus(manifest, corpus_root, settings)` returns `dict[StrategyId, list[Chunk]]` for all 120 documents.
- `Chunk.content_hash` is the SHA-256 of `embed_text` and is the embedding cache key. It already differs between the structural and contextual strategies for the same slice.
- `Chunk.embed_text` is exactly the string to send to `text-embedding-3-large`. Nothing further should be prepended to it.
- Token budgets went through `HeuristicCounter`, so a chunk's true token count may differ from its target by up to ~15% at the median. `text-embedding-3-large` accepts 8191 tokens and the p90 chunk is far below that, but Plan 3 should still fail loudly rather than truncate if the API rejects one.
- The three strategies share an embedding cache. A run that embeds all three costs the union of their `content_hash` sets, not three times one strategy.

## Known Gaps, Deliberately Left

- **Chunks are not persisted.** They are produced, measured and discarded. Where they live is a retrieval-story decision, and inventing a table now would be guessing at what the index wants.
- **`split_step_lists` detects a cut list by its first step not being step 1.** A chunk that ends partway through a list but starts at step 1 is not counted. Tightening it needs the list's true extent, which means a list-extent pass the chunkers do not currently share.
- **The heuristic ratios are corpus-specific.** They were measured on this corpus and are re-measured by `just chunk-stats --calibrate`. A corpus in a third language needs a new ratio, and the default would otherwise size its chunks as if it were English.
