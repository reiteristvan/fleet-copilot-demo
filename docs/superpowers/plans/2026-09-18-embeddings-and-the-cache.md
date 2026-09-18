# Embeddings and the Embedding Cache — Implementation Plan (3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed every chunk the three strategies produce with `text-embedding-3-large`, in token-budgeted async batches, and cache the vectors in Postgres by `content_hash` so that a re-run with no content change makes **zero** embedding calls.

**Architecture:** An `EmbeddingCache` over a Postgres table keyed by `(content_hash, model_id)`, read in one round trip per batch. An `Embedder` Protocol with an `AzureEmbedder` that wraps `AsyncAzureOpenAI` authenticated by an Entra token provider, and a `StubEmbedder` that counts calls so the acceptance criterion is testable without a bill. Batching is a pure function over chunks: a token budget, not a count. The SDK owns 429 backoff.

**Tech Stack:** Python 3.12, `openai` 3.x (`AsyncAzureOpenAI`), `azure-identity` (`aio.get_bearer_token_provider`), psycopg 3, Alembic (hand-written SQL, ADR 0004), pgvector, pydantic v2, pytest + pytest-asyncio, mypy --strict, ruff.

**Spec:** `docs/adr/0007-the-embedding-store.md`, with `docs/adr/0006-the-chunk-contract.md` for what `content_hash` covers and `docs/adr/0002-keyless-azure-access.md` for how the client authenticates.

**Depends on Plan 2** being merged: `Chunk`, the three strategies, and `chunk_corpus()`.

**Scope boundary.** This plan embeds and caches. It builds no index and runs no similarity search — pgvector's hnsw and ivfflat cap at 2000 dimensions and these vectors are 3072, so choosing `halfvec` or a reduced-dimension copy is a decision the retrieval story makes with its query patterns in hand (ADR 0007).

## Global Constraints

- **`just check` is the gate.** Paste its output; never assert it passed.
- **Never weaken a gate to make a change land.**
- **Every `noqa` and `type: ignore` carries a code and a reason.**
- **Do not touch without an explicit human go-ahead:** the `[tool.*]` sections of `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/`, `uv.lock` by hand, `CLAUDE.md`.
- **Python 3.12 floor.** `mypy --strict` covers `src` and `tests`; every test annotated `-> None`.
- **Domain models are pydantic v2, frozen, `extra="forbid"`.**
- **Nothing blocking runs in a coroutine.** psycopg calls go through `asyncio.to_thread` or `psycopg.AsyncConnection`; the `ASYNC` ruff rules enforce it.
- **Async tests opt in** with `@pytest.mark.asyncio`.
- **Never introduce key-based auth to an Azure data plane.** No `api_key`, no `AzureKeyCredential`. The account has `disableLocalAuth: true`, so a key would fail at runtime rather than in review, and `tests/test_no_key_based_auth.py` greps for it.
- **Migrations are hand-written SQL through `op.execute`** (ADR 0004). Revision ids follow `000N_name`; the next is `0007_embeddings` in `migrations/versions/20260918_0007_embeddings.py`, revising `0006_as_of_function`.
- **Database tests skip rather than fail when no database is reachable**, matching the existing fixtures in `tests/fleet/`.
- **CI never calls Azure.** Every test in this plan uses `StubEmbedder` or a local Postgres.

## Verified facts this plan is built on

Checked against the live subscription and the installed SDKs, not assumed.

| Fact | Value |
| --- | --- |
| Deployment name / model | `embeddings` / `text-embedding-3-large` version `1` |
| SKU and capacity | `Standard`, 50 → **50,000 tokens per minute** |
| Dimensions | 3072 |
| Entra scope | `https://cognitiveservices.azure.com/.default` |
| Role already assigned in `rbac.bicep` | Cognitive Services OpenAI User (`5e0bd9bd-…`) |
| API version in `Settings` | `2024-10-21` |
| `openai` SDK | 3.15.0; `AsyncAzureOpenAI(azure_endpoint, azure_deployment, api_version, azure_ad_token_provider)` |
| SDK retry | retries 408/409/429/5xx with exponential backoff, honours `Retry-After`; `DEFAULT_MAX_RETRIES = 2` |
| Azure per-request input cap | 2048 inputs |
| Per-input token cap | 8191 |
| pgvector image | `pgvector/pgvector:pg16` (extension shipped, **not** enabled) |
| pgvector index dimension cap | 2000 for `vector`, 4000 for `halfvec` — 3072 fits neither `vector` index |
| Estimated full-corpus cost | ~112,000 tokens ≈ **$0.015** |

The cost line is why ADR 0007 does not justify the cache on money. The justification is the acceptance criterion and the determinism it protects.

## File Structure

| File | Responsibility |
| --- | --- |
| `migrations/versions/20260918_0007_embeddings.py` (new) | `CREATE EXTENSION vector`, the cache table, the grant. |
| `src/fleet_copilot/ingest/embedding/__init__.py` (new) | Re-exports. |
| `src/fleet_copilot/ingest/embedding/models.py` (new) | `EmbeddingRecord`, `EmbedReport`. |
| `src/fleet_copilot/ingest/embedding/batching.py` (new) | `batch_by_tokens()` — pure. |
| `src/fleet_copilot/ingest/embedding/store.py` (new) | `EmbeddingStore` over Postgres. |
| `src/fleet_copilot/ingest/embedding/client.py` (new) | `Embedder` Protocol, `AzureEmbedder`, `StubEmbedder`. |
| `src/fleet_copilot/ingest/embedding/run.py` (new) | `embed_chunks()` — cache-first, batch, persist. |
| `src/fleet_copilot/config.py` (modify) | `azure_openai_embedding_dimensions`, `embedding_batch_tokens`, `embedding_max_retries`. |
| `scripts/embed_corpus.py` (new) | Argument-parsing shim. |
| `justfile`, `README.md`, `docs/chunking.md` (modify) | The recipe and the numbers. |

---

### Task 1: The migration

**Files:**
- Create: `migrations/versions/20260918_0007_embeddings.py`
- Test: `tests/ingest/embedding/test_migration.py`

**Interfaces:**
- Produces: table `ingest.embedding_cache` with columns `content_hash text primary key`-ish composite, `model_id text`, `dimensions int`, `embedding vector(3072)`, `created_at timestamptz`.

- [ ] **Step 1: Write the migration**

Create `migrations/versions/20260918_0007_embeddings.py`:

```python
"""Cache embeddings by the hash of what was actually embedded.

The corpus is deterministic and the chunkers are pure, so the same chunk
produces the same vector on every run. The acceptance criterion for this story
is that a clean re-run makes zero embedding calls -- which is a statement about
determinism rather than about cost: at roughly 112,000 tokens a full run costs
about a cent and a half, and three chunking strategies are about to be compared
on inputs that must not move between runs.

No vector index. Every read here is an equality match on the primary key;
similarity search belongs to the retrieval story, which will have to choose
halfvec or a reduced-dimension copy because pgvector's hnsw and ivfflat cap at
2000 dimensions and these are 3072 (ADR 0007).

Revision ID: 0007_embeddings
Revises: 0006_as_of_function
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_embeddings"
down_revision: str | None = "0006_as_of_function"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READONLY_ROLE = "copilot_ro"


def upgrade() -> None:
    # The pgvector image ships the extension but does not enable it in the
    # database. Without this the column type below is rejected as an unknown
    # type, which reads like a typo rather than a missing extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("CREATE SCHEMA IF NOT EXISTS ingest")

    op.execute(
        """
        CREATE TABLE ingest.embedding_cache (
            content_hash  text        NOT NULL,
            model_id      text        NOT NULL,
            dimensions    integer     NOT NULL,
            embedding     vector(3072) NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT embedding_cache_pkey PRIMARY KEY (content_hash, model_id),
            CONSTRAINT embedding_cache_hash_is_sha256
                CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT embedding_cache_dimensions_positive
                CHECK (dimensions > 0)
        )
        """
    )

    # The model is in the key, not only in a column: a different deployment must
    # miss and re-embed rather than silently return vectors from another model,
    # whose numbers would look entirely reasonable and rank nothing correctly.
    op.execute(
        """
        COMMENT ON TABLE ingest.embedding_cache IS
        'Vectors keyed by the sha256 of embed_text and the model that produced them.'
        """
    )

    # copilot_ro answers questions; it has no business reading raw vectors, and
    # ADR 0002's boundary is only real if every new object is granted
    # deliberately rather than by default.
    op.execute(f"REVOKE ALL ON SCHEMA ingest FROM {READONLY_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingest.embedding_cache")
    # The schema and the extension are left in place. Another migration may have
    # put something in the schema, and dropping an extension that a different
    # table's column type depends on fails in a way that is hard to read.
```

- [ ] **Step 2: Apply it forward and back**

```bash
just up
just db-migrate
just db-reset
```

Expected: `db-reset` runs `downgrade base` then `upgrade head` without error, which proves the migration applies from scratch.

- [ ] **Step 3: Write the schema test**

Create `tests/ingest/embedding/test_migration.py`, following the skip-not-fail fixture style already in `tests/fleet/`:

```python
"""What the embedding cache table guarantees."""

from __future__ import annotations

import pytest


@pytest.mark.usefixtures("database")
def test_the_vector_extension_is_enabled(connection: object) -> None:
    """The image ships pgvector but does not enable it.

    Without the extension the column type is rejected as unknown, which reads
    like a typo in the migration rather than a missing CREATE EXTENSION.
    """
    with connection.cursor() as cursor:  # type: ignore[attr-defined]  # psycopg conn fixture
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None


@pytest.mark.usefixtures("database")
def test_a_non_sha256_hash_is_refused(connection: object) -> None:
    """The key is a content hash; anything else in it is a bug upstream that
    would otherwise sit in the table looking like data."""
    import psycopg

    with connection.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]  # psycopg conn fixture
        cursor.execute(
            "INSERT INTO ingest.embedding_cache "
            "(content_hash, model_id, dimensions, embedding) VALUES (%s, %s, %s, %s)",
            ("not-a-hash", "text-embedding-3-large", 3, "[1,2,3]"),
        )


@pytest.mark.usefixtures("database")
def test_the_same_hash_under_two_models_is_two_rows(connection: object) -> None:
    """A model change must miss the cache, not return another model's vectors."""
    with connection.cursor() as cursor:  # type: ignore[attr-defined]  # psycopg conn fixture
        for model in ("model-a", "model-b"):
            cursor.execute(
                "INSERT INTO ingest.embedding_cache "
                "(content_hash, model_id, dimensions, embedding) VALUES (%s, %s, %s, %s)",
                ("a" * 64, model, 3, "[1,2,3]"),
            )
        cursor.execute(
            "SELECT count(*) FROM ingest.embedding_cache WHERE content_hash = %s", ("a" * 64,)
        )
        assert cursor.fetchone()[0] == 2
```

Read `tests/fleet/conftest.py` first and reuse its `database` / `connection` fixtures verbatim rather than writing new ones; if they live in one module, move them to `tests/conftest.py` — that is the second consumer the existing comment there anticipated.

The `vector(3072)` column accepts a 3-element literal only if the column is declared with matching dimensions; use `[1,2,3]` against a test-only table or pad the literal to 3072 with a helper. Prefer the helper: `"[" + ",".join(["0"] * 3072) + "]"`, defined once at the top of the module.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/ingest/embedding/test_migration.py -v`
Expected: 3 passed, or 3 skipped if no database is reachable.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/20260918_0007_embeddings.py tests/ingest/embedding/
git commit -m "feat(ingest): add the embedding cache table

Keyed by content_hash and model id together: a different deployment must miss
and re-embed rather than silently return another model's vectors, whose numbers
would look reasonable and rank nothing correctly.

No vector index. Every read is an equality match on the primary key, and
similarity search belongs to the retrieval story -- which will have to choose
halfvec or a reduced-dimension copy, because pgvector's hnsw and ivfflat cap at
2000 dimensions and these are 3072.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Token-budgeted batching

**Files:**
- Create: `src/fleet_copilot/ingest/embedding/__init__.py`, `src/fleet_copilot/ingest/embedding/batching.py`
- Test: `tests/ingest/embedding/test_batching.py`

**Interfaces:**
- Consumes: `Chunk`, `TokenCounter`.
- Produces: `batch_by_tokens(chunks, *, budget_tokens, max_inputs=2048, counter=None) -> tuple[tuple[Chunk, ...], ...]`; `MAX_INPUTS_PER_REQUEST: Final = 2048`; `MAX_TOKENS_PER_INPUT: Final = 8191`; `class OversizedChunkError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/embedding/test_batching.py`:

```python
"""How chunks are grouped into requests."""

from __future__ import annotations

from datetime import date

import pytest

from fleet_copilot.ingest.embedding.batching import (
    MAX_INPUTS_PER_REQUEST,
    OversizedChunkError,
    batch_by_tokens,
)
from fleet_copilot.ingest.models import Chunk, StrategyId


def a_chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        doc_id="d",
        type="service_manual",
        language="en",
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/embedding/test_batching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.embedding'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/embedding/batching.py`**

```python
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
        too_large = current and current_tokens + cost > budget_tokens
        if too_many or too_large:
            batches.append(tuple(current))
            current = []
            current_tokens = 0

        current.append(chunk)
        current_tokens += cost

    if current:
        batches.append(tuple(current))
    return tuple(batches)
```

Create `src/fleet_copilot/ingest/embedding/__init__.py`:

```python
"""Turn chunks into vectors, once each."""
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/embedding/test_batching.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/embedding/ tests/ingest/embedding/test_batching.py
git commit -m "feat(embedding): batch by tokens rather than by count

The Azure throttle is tokens per minute and this corpus holds chunks from 40 to
over a thousand tokens, so a fixed-count batch sends an unpredictable number of
tokens and the request that trips the limit has nothing to do with anything
visible.

A chunk over the model's 8191-token cap raises rather than being truncated.
Truncating would embed a prefix and cache the vector under a hash of the whole
text -- a wrong answer every later run would reuse without calling the model.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The embedder

**Files:**
- Create: `src/fleet_copilot/ingest/embedding/client.py`
- Modify: `src/fleet_copilot/config.py`
- Test: `tests/ingest/embedding/test_client.py`

**Interfaces:**
- Produces: `class Embedder(Protocol)` with `async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]` and a `model_id` property; `class AzureEmbedder`; `class StubEmbedder(dimensions=8)` exposing `.calls` and `.inputs`.
- `Settings` gains `azure_openai_embedding_dimensions: int = 3072`, `embedding_batch_tokens: int = 8000`, `embedding_max_retries: int = 6`.

- [ ] **Step 1: Add the settings**

In `src/fleet_copilot/config.py`, after `azure_openai_api_version`:

```python
    # text-embedding-3-large's native width. Not reduced with the `dimensions`
    # parameter: shortening is one-way, and a full vector can be truncated later
    # while a short one cannot be grown back (ADR 0007).
    azure_openai_embedding_dimensions: int = 3072

    # Well under the deployment's 50,000 tokens per minute, so a single batch
    # cannot consume the whole minute's budget and stall everything behind it.
    embedding_batch_tokens: int = 8000

    # Raised from the SDK's default of 2. The SDK's own backoff reads the
    # Retry-After header Azure sends on a 429; a hand-rolled one ignores it and
    # retries early, which makes the throttling worse (ADR 0007).
    embedding_max_retries: int = 6
```

- [ ] **Step 2: Write the failing tests**

Create `tests/ingest/embedding/test_client.py`:

```python
"""The embedder interface, and the stub the rest of the suite runs against."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.embedding.client import Embedder, StubEmbedder


@pytest.mark.asyncio
async def test_the_stub_returns_one_vector_per_input() -> None:
    stub = StubEmbedder(dimensions=8)

    vectors = await stub.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert all(len(vector) == 8 for vector in vectors)


@pytest.mark.asyncio
async def test_the_stub_is_deterministic_for_the_same_text() -> None:
    """The cache is only testable if the same text gives the same vector."""
    stub = StubEmbedder(dimensions=8)

    first = await stub.embed(["alpha"])
    second = await stub.embed(["alpha"])

    assert first == second


@pytest.mark.asyncio
async def test_the_stub_counts_calls_and_inputs() -> None:
    """The acceptance criterion is 'zero embedding calls', so something has to
    be able to count them without a bill."""
    stub = StubEmbedder(dimensions=8)

    await stub.embed(["a", "b"])
    await stub.embed(["c"])

    assert stub.calls == 2
    assert stub.inputs == 3


def test_the_stub_satisfies_the_protocol() -> None:
    assert isinstance(StubEmbedder(), Embedder)


def test_the_azure_embedder_never_takes_a_key() -> None:
    """ADR 0002. The account has disableLocalAuth, so a key fails at runtime
    rather than in review -- this moves the failure to import time."""
    import inspect

    from fleet_copilot.ingest.embedding import client

    source = inspect.getsource(client)
    assert "api_key" not in source
    assert "AzureKeyCredential" not in source
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/ingest/embedding/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.embedding.client'`

- [ ] **Step 4: Implement `src/fleet_copilot/ingest/embedding/client.py`**

```python
"""What turns text into vectors.

Two implementations behind one Protocol, the same shape as the parsers and the
caches: :class:`StubEmbedder` is deterministic, offline and counts its calls, so
the whole suite -- including the "zero calls on a re-run" criterion -- runs
without an Azure account; :class:`AzureEmbedder` is what a real run uses.

The SDK is imported inside the method that needs it, so this module stays
importable without `openai`.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from fleet_copilot.config import Settings

Vector = tuple[float, ...]


@runtime_checkable
class Embedder(Protocol):
    """Embeds a batch of strings, in order."""

    @property
    def model_id(self) -> str: ...

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


class StubEmbedder:
    """Deterministic fake vectors derived from the text. Implements Embedder.

    Derived from a hash rather than random so the same text gives the same
    vector across processes: a cache test whose "hit" returned a different
    vector from its "miss" would pass for the wrong reason.
    """

    def __init__(self, dimensions: int = 8, model_id: str = "stub") -> None:
        self._dimensions = dimensions
        self._model_id = model_id
        self.calls = 0
        self.inputs = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        self.calls += 1
        self.inputs += len(texts)
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> Vector:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat the digest until it covers the requested width; four bytes per
        # float, unpacked as unsigned ints and scaled into [0, 1).
        needed = self._dimensions * 4
        raw = (digest * (needed // len(digest) + 1))[:needed]
        return tuple(value / 0xFFFFFFFF for value in struct.unpack(f">{self._dimensions}I", raw))


class AzureEmbedder:
    """Calls the deployed text-embedding-3-large. Implements Embedder.

    Authentication is an Entra token provider and nothing else: ADR 0002
    disables local auth on the account, so a key here would not fail in review,
    it would fail at runtime.

    Retry is the SDK's. It already backs off on 408, 409, 429 and 5xx and reads
    the Retry-After header Azure sends, which a hand-rolled exponential backoff
    ignores -- retrying early and making the throttle worse. What this class
    owns is `max_retries`; what the caller owns is the batch size, which is what
    decides whether a 429 happens at all (ADR 0007).
    """

    SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self, settings: Settings) -> None:
        if not settings.azure_openai_endpoint:
            msg = (
                "AZURE_OPENAI_ENDPOINT is not set. Populate it from "
                "`./infra/deploy.sh dev`, which prints it as an export line."
            )
            raise ValueError(msg)
        self._settings = settings

    @property
    def model_id(self) -> str:
        """The deployment, which is what the cache key records.

        Deliberately the deployment name and not the model name: two deployments
        of the same model can differ in version, and a cache that could not tell
        them apart would serve one's vectors for the other.
        """
        return self._settings.azure_openai_embedding_deployment

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed ``texts`` in one request, preserving order."""
        from azure.identity.aio import get_bearer_token_provider
        from openai import AsyncAzureOpenAI

        from fleet_copilot.credentials import get_async_credential

        settings = self._settings
        credential = get_async_credential()
        async with credential:
            client = AsyncAzureOpenAI(
                azure_endpoint=str(settings.azure_openai_endpoint),
                azure_deployment=settings.azure_openai_embedding_deployment,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=get_bearer_token_provider(credential, self.SCOPE),
                max_retries=settings.embedding_max_retries,
            )
            async with client:
                response = await client.embeddings.create(
                    input=list(texts),
                    model=settings.azure_openai_embedding_deployment,
                )

        # The API documents the order as matching the input, but it also returns
        # an explicit index; sorting on it costs nothing and removes a class of
        # bug where every vector is attributed to the wrong chunk and nothing
        # looks broken until a citation is read.
        ordered = sorted(response.data, key=lambda item: item.index)
        return tuple(tuple(item.embedding) for item in ordered)
```

- [ ] **Step 5: Run them and watch them pass**

Run: `uv run pytest tests/ingest/embedding/test_client.py -v && uv run mypy`
Expected: all pass, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/embedding/client.py src/fleet_copilot/config.py \
        tests/ingest/embedding/test_client.py
git commit -m "feat(embedding): call text-embedding-3-large with an Entra token

Retry stays the SDK's. It already backs off on 429 and reads the Retry-After
header Azure sends; a hand-rolled exponential backoff ignores that header and
retries early, which makes the throttling worse and is indistinguishable from a
bug under load. max_retries goes to 6; the batch size is what actually decides
whether a 429 happens.

Vectors are reordered on the response index rather than trusted to arrive in
input order -- the failure it prevents attributes every vector to the wrong
chunk and looks like nothing at all until a citation is read.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The store

**Files:**
- Create: `src/fleet_copilot/ingest/embedding/models.py`, `src/fleet_copilot/ingest/embedding/store.py`
- Test: `tests/ingest/embedding/test_store.py`

**Interfaces:**
- Produces: `class EmbeddingRecord` (frozen: `content_hash`, `model_id`, `dimensions`, `embedding`); `class EmbeddingStore(database_url)` with `async def known(hashes, model_id) -> set[str]` and `async def put_many(records) -> int`; `class EmbedReport` (frozen).

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/embedding/test_store.py`, reusing the `database` fixture from Task 1:

```python
"""Reading and writing the cache."""

from __future__ import annotations

import pytest

from fleet_copilot.ingest.embedding.models import EmbeddingRecord
from fleet_copilot.ingest.embedding.store import EmbeddingStore

MODEL = "embeddings"


def a_record(seed: str, dimensions: int = 3072) -> EmbeddingRecord:
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
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="dimensions"):
        EmbeddingRecord.model_validate(
            {
                "content_hash": "a" * 64,
                "model_id": MODEL,
                "dimensions": 4,
                "embedding": (0.0, 0.0),
            }
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
async def test_what_goes_in_is_known_afterwards(database_url: str) -> None:
    store = EmbeddingStore(database_url)

    written = await store.put_many([a_record("a"), a_record("b")])

    assert written == 2
    assert await store.known({"a" * 64, "b" * 64, "c" * 64}, MODEL) == {"a" * 64, "b" * 64}


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
async def test_writing_the_same_hash_twice_is_not_an_error(database_url: str) -> None:
    """Two strategies can produce the same chunk text with no header, and a run
    interrupted and restarted will re-offer what it already wrote."""
    store = EmbeddingStore(database_url)

    await store.put_many([a_record("d")])
    await store.put_many([a_record("d")])

    assert await store.known({"d" * 64}, MODEL) == {"d" * 64}


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
async def test_another_model_does_not_see_these_rows(database_url: str) -> None:
    store = EmbeddingStore(database_url)

    await store.put_many([a_record("e")])

    assert await store.known({"e" * 64}, "some-other-deployment") == set()


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
async def test_asking_about_nothing_touches_no_database(database_url: str) -> None:
    """An empty batch is common at the end of a run; a round trip for it is
    pure latency."""
    assert await EmbeddingStore(database_url).known(set(), MODEL) == set()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/embedding/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/embedding/models.py`**

```python
"""What a cached embedding is, and what a run of them did."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbeddingRecord(BaseModel):
    """One vector, ready to be written to the cache."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    model_id: Annotated[str, Field(min_length=1)]
    dimensions: Annotated[int, Field(gt=0)]
    embedding: tuple[float, ...]

    @model_validator(mode="after")
    def _length_matches_dimensions(self) -> Self:
        """Catch a short vector here rather than in the database.

        Postgres rejects it with a message about the column's declared width,
        three layers away from the code that built the row and with no mention
        of which chunk it came from.
        """
        if len(self.embedding) != self.dimensions:
            msg = (
                f"embedding has {len(self.embedding)} values but dimensions says {self.dimensions}"
            )
            raise ValueError(msg)
        return self

    def literal(self) -> str:
        """Render the vector as the text form pgvector parses."""
        return "[" + ",".join(repr(value) for value in self.embedding) + "]"


class EmbedReport(BaseModel):
    """What an embedding run did, or would have done."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: int
    unique: int
    cached: int
    embedded: int
    requests: int
    dry_run: bool

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        verb = "would embed" if self.dry_run else "embedded"
        return (
            f"{self.chunks} chunks, {self.unique} distinct, {self.cached} already cached, "
            f"{verb} {self.embedded} in {self.requests} requests"
        )
```

Implement `src/fleet_copilot/ingest/embedding/store.py`:

```python
"""The embedding cache, over Postgres.

psycopg's async connection is used rather than the sync one in a thread: this
runs interleaved with HTTP requests to the embedding endpoint, and a thread per
query would hold a pool slot for the whole round trip.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from fleet_copilot.ingest.embedding.models import EmbeddingRecord


class EmbeddingStore:
    """Reads and writes ``ingest.embedding_cache``."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url

    async def known(self, hashes: Collection[str], model_id: str) -> set[str]:
        """Return the subset of ``hashes`` already embedded by ``model_id``.

        One round trip for the whole batch rather than one per chunk: at a few
        hundred chunks the per-query latency dominates everything else the run
        does.
        """
        if not hashes:
            return set()

        import psycopg

        async with await psycopg.AsyncConnection.connect(self._url) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT content_hash FROM ingest.embedding_cache "
                    "WHERE model_id = %s AND content_hash = ANY(%s)",
                    (model_id, list(hashes)),
                )
                rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def put_many(self, records: Sequence[EmbeddingRecord]) -> int:
        """Write ``records``, ignoring any whose key is already present.

        ON CONFLICT DO NOTHING rather than DO UPDATE: the same key under the
        same model is by definition the same vector, so a second write is a
        restart or a duplicate chunk, not a correction.
        """
        if not records:
            return 0

        import psycopg

        async with await psycopg.AsyncConnection.connect(self._url) as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO ingest.embedding_cache "
                    "(content_hash, model_id, dimensions, embedding) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    [
                        (record.content_hash, record.model_id, record.dimensions, record.literal())
                        for record in records
                    ],
                )
            await connection.commit()
        return len(records)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/embedding/test_store.py -v`
Expected: 5 passed, or skipped without a database.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/embedding/models.py \
        src/fleet_copilot/ingest/embedding/store.py tests/ingest/embedding/test_store.py
git commit -m "feat(embedding): read and write the cache in one round trip per batch

known() asks about a whole batch at once: at a few hundred chunks the per-query
latency dominates everything else the run does.

A record validates its own width before Postgres sees it. Postgres rejects a
short vector with a message about the column, three layers from the code that
built the row and with no mention of which chunk it came from.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The run, and the acceptance criterion

**Files:**
- Create: `src/fleet_copilot/ingest/embedding/run.py`
- Test: `tests/ingest/embedding/test_run.py`

**Interfaces:**
- Produces: `async def embed_chunks(chunks, store, embedder, *, dimensions, budget_tokens, dry_run=False, concurrency=4) -> EmbedReport`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/embedding/test_run.py`:

```python
"""The run, and the criterion the story is judged on."""

from __future__ import annotations

from datetime import date

import pytest

from fleet_copilot.ingest.embedding.client import StubEmbedder
from fleet_copilot.ingest.embedding.models import EmbeddingRecord
from fleet_copilot.ingest.embedding.run import embed_chunks
from fleet_copilot.ingest.models import Chunk, StrategyId

DIMENSIONS = 8


class MemoryStore:
    """An EmbeddingStore with the database taken out."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EmbeddingRecord] = {}

    async def known(self, hashes: object, model_id: str) -> set[str]:
        assert hasattr(hashes, "__iter__")
        return {h for h in hashes if (h, model_id) in self.rows}  # type: ignore[union-attr]  # duck-typed fake

    async def put_many(self, records: object) -> int:
        for record in records:  # type: ignore[union-attr]  # duck-typed fake
            self.rows[(record.content_hash, record.model_id)] = record
        return len(list(records))  # type: ignore[call-overload]  # duck-typed fake


def some_chunks(count: int = 12) -> list[Chunk]:
    return [
        Chunk(
            doc_id="d",
            type="service_manual",
            language="en",
            revision=1,
            effective_date=date(2026, 1, 1),
            chunk_index=index,
            strategy=StrategyId.STRUCTURAL,
            text=f"passage number {index} " + "x" * 200,
            start=0,
            end=len(f"passage number {index} " + "x" * 200),
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/embedding/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.embedding.run'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/embedding/run.py`**

```python
"""Embed a set of chunks, skipping everything already cached.

Deduplicated before anything is sent: three strategies produce chunks over the
same documents, and two of them can produce byte-identical embed_text. Paying
twice for one vector is the small cost; writing two rows that must stay in step
is the larger one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
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

    async def known(self, hashes: object, model_id: str) -> set[str]: ...

    async def put_many(self, records: object) -> int: ...


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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/embedding/ -v && uv run mypy`
Expected: all pass, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/embedding/run.py tests/ingest/embedding/test_run.py
git commit -m "feat(embedding): embed what is not cached, and prove a re-run is free

Deduplicated by content_hash before anything is sent: three strategies chunk the
same documents and two of them can produce byte-identical embed_text. Paying
twice is the small cost; two rows that must stay in step is the larger one.

Vectors are zipped strictly to their batch and written per batch rather than at
the end, so an interrupted run keeps what it paid for.

The acceptance criterion is a test: the second run over unchanged chunks makes
zero calls, counted by a stub rather than inferred from a bill.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire it to the corpus

**Files:**
- Create: `scripts/embed_corpus.py`
- Modify: `src/fleet_copilot/ingest/run.py`, `justfile`, `pyproject.toml` (dev group), `.env.example`
- Test: `tests/ingest/test_run.py`

**Interfaces:**
- Produces: `async def embed_corpus(manifest, corpus_root, settings, *, dry_run) -> dict[StrategyId, EmbedReport]`; `just embed-corpus`.

- [ ] **Step 1: Add the dependency**

```bash
uv add --dev "openai>=3.15"
```

Dev-only, like the other ingest dependencies: embedding is an offline pipeline step and the API image installs with `--no-dev`.

- [ ] **Step 2: Add the run function**

Append to `src/fleet_copilot/ingest/run.py`:

```python
async def embed_corpus(
    manifest: Manifest,
    corpus_root: Path,
    settings: Settings,
    *,
    dry_run: bool = True,
) -> dict[StrategyId, EmbedReport]:
    """Chunk the corpus and embed every strategy's chunks, cache-first.

    One store and one embedder across all three strategies: they share the cache
    by design, so embedding all three costs the union of their content hashes
    rather than three separate runs.
    """
    from fleet_copilot.ingest.embedding.client import AzureEmbedder
    from fleet_copilot.ingest.embedding.run import embed_chunks
    from fleet_copilot.ingest.embedding.store import EmbeddingStore

    by_strategy = await chunk_corpus(manifest, corpus_root, settings)
    store = EmbeddingStore(settings.database_url)
    embedder = AzureEmbedder(settings)

    reports: dict[StrategyId, EmbedReport] = {}
    for strategy, chunks in by_strategy.items():
        reports[strategy] = await embed_chunks(
            chunks,
            store,
            embedder,
            dimensions=settings.azure_openai_embedding_dimensions,
            budget_tokens=settings.embedding_batch_tokens,
            dry_run=dry_run,
        )
    return reports
```

Add the imports it needs: `from fleet_copilot.ingest.embedding.models import EmbedReport`.

- [ ] **Step 3: Write `scripts/embed_corpus.py`**

```python
"""Embed the corpus under all three chunking strategies.

    python scripts/embed_corpus.py            report what would be embedded
    python scripts/embed_corpus.py --apply    actually call the model

Needs the local database up (`just up && just db-migrate`), the layout cache
populated (`just corpus-parse --apply`), and Cognitive Services OpenAI User on
your own principal.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest.run import embed_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="embed for real; without it nothing is called"
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(None))
        settings = load_settings()
        reports = asyncio.run(
            embed_corpus(manifest, corpus_root(None), settings, dry_run=not args.apply)
        )
    except (CorpusDataError, SettingsError) as error:
        print(f"embed failed: {error}", file=sys.stderr)
        return 2

    for strategy, report in reports.items():
        print(f"{strategy.value:12} {report.describe()}")
    if any(report.dry_run for report in reports.values()):
        print("nothing was called; re-run with --apply to embed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the justfile recipe**

After `chunk-stats`:

```make
# Dry-run the embedding pass: `just embed-corpus --apply` calls the model.
embed-corpus *args:
    uv run python scripts/embed_corpus.py {{args}}
```

- [ ] **Step 5: Run it for real, then again**

```bash
just up && just db-migrate
just embed-corpus
just embed-corpus --apply
just embed-corpus --apply
```

Expected: the second `--apply` reports `embedded 0` and `already cached` equal to the distinct count for every strategy. **That line is this plan's deliverable** and the story's second acceptance criterion.

- [ ] **Step 6: Run the full gate**

Run: `just check`
Then: `uv run pre-commit run --all-files`
Expected: both clean. Paste the output.

- [ ] **Step 7: Update the docs and commit**

Add the embedding numbers to `docs/chunking.md` — distinct hashes per strategy, the union across all three, the requests made and the measured cost — and a row to the README capability table.

```bash
git add src/fleet_copilot/ingest/run.py scripts/embed_corpus.py justfile \
        pyproject.toml uv.lock .env.example docs/chunking.md README.md tests/
git commit -m "feat(ingest): embed the corpus under every strategy, once

One store and one embedder across all three: they share the cache by design, so
embedding all three costs the union of their content hashes rather than three
separate runs.

The second --apply run embeds nothing, which is the story's acceptance
criterion and the only externally visible proof that content_hash describes
what was actually sent to the model.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Before Handing Back

1. `just check` — all three stages, output pasted, not asserted.
2. `uv run pre-commit run --all-files`.
3. `just db-reset` — the migration applies from scratch, forward and back.
4. `just embed-corpus --apply` run twice: the second reports `embedded 0` for all three strategies.
5. `tests/test_no_key_based_auth.py` passes, with the new `openai` client in `src`.
6. Confirm the scope held: **no vector index was created and no similarity search was written.** Both belong to the retrieval story.

## What the Retrieval Story Inherits

- `ingest.embedding_cache` holds `vector(3072)` keyed by `(content_hash, model_id)`, with **no index**. pgvector's hnsw and ivfflat cap at 2000 dimensions, so the retrieval story must choose `halfvec(3072)` (indexable to 4000) or a reduced-dimension copy. ADR 0007 deliberately leaves that open.
- A chunk's vector is found by recomputing `content_hash` from the chunk; nothing stores chunks themselves yet.
- The three strategies' vectors live in one table and are told apart only by which `content_hash` set you ask for. Story 3.3 compares them by asking with a different set, not by reading a different table.

## Known Gaps, Deliberately Left

- **No pruning.** A corpus or chunker change strands the old vectors under hashes nothing asks for. Harmless and accumulating, like the layout cache.
- **`concurrency` is a constant, not adaptive.** Four in-flight batches of 8000 tokens sit comfortably under the 50,000 TPM deployment; a deployment with different capacity would want it derived from the capacity rather than assumed.
- **The dry run does not verify the endpoint answers.** It reports what would be sent without proving a token can be acquired, so the first real failure of a misconfigured principal appears on `--apply`.
