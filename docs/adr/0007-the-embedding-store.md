# 7. Where embeddings live, and who owns the backoff

Date: 2026-09-18

## Status

Accepted

## Context

Three chunking strategies produce chunks over the same 120 documents, keyed for
embedding by `content_hash` — the SHA-256 of what is actually sent to the model
(ADR 0006). The deployed model is `text-embedding-3-large` on a Standard SKU
with 50,000 tokens per minute, returning 3072 dimensions.

The story asks for the embeddings to be cached in Postgres "so re-runs are
free", and for retry with backoff on 429. Both deserve a second look before
being implemented as written.

On cost: the corpus body is 34,205 tokens. Across three strategies — the fixed
window adds about 18% through overlap, the contextual header adds a dozen tokens
per chunk — a full embedding run is roughly 112,000 tokens, or about **1.5
cents** at $0.13 per million. "Free re-runs" is not the argument. The argument
is the acceptance criterion, which says a clean re-run must make *zero* calls,
and that is a statement about determinism: three strategies are going to be
compared, and a comparison whose inputs are re-fetched between runs has a
variable in it that nobody declared.

On backoff: the `openai` SDK already retries 408, 409, 429 and 5xx with
exponential backoff and jitter, and it reads the `Retry-After` header. Azure
OpenAI sends that header on a 429 with the real wait.

## Decision

**The cache is a table keyed by `content_hash`, and stores `vector(3072)` at
full width with no vector index.** It is a lookup table, not a search index:
every read is an equality match on the primary key. Similarity search belongs to
the retrieval story, and it will need its own decision, because pgvector's hnsw
and ivfflat indexes cap at 2000 dimensions — 3072 does not fit. That story can
choose `halfvec`, which indexes up to 4000, or a reduced-dimension copy. Neither
choice is forced now, and making it now would be guessing at what the index
wants.

**Dimensions are not reduced at embedding time.** `text-embedding-3-large`
accepts a `dimensions` parameter and Matryoshka training makes the shortened
vectors usable. It is still one-way: a full 3072-dimension vector can be
truncated and renormalised later, and a 1024-dimension one cannot be grown back.
Storing full width costs 12 KB per chunk and roughly 9 MB for the whole corpus
across three strategies, which is not a number worth optimising against an
irreversible loss.

**The SDK owns the 429 backoff, and this project does not reimplement it.**
A hand-rolled exponential backoff ignores `Retry-After` and therefore retries
before the window has passed, which makes the throttling worse rather than
better and is indistinguishable from a bug under load. `max_retries` is raised
from its default of 2 and the reason is recorded at the call site. What this
project *does* own is the batch size, because that is what decides whether a 429
happens at all.

**A batch is bounded by tokens, not by a count.** Azure accepts up to 2048
inputs per embedding request, but the throttle is on tokens per minute. Batching
by count sends a wildly variable number of tokens per request — this corpus has
chunks from 40 tokens to over 1000 — so the request that finally trips the limit
is unpredictable and unrelated to anything a reader can see. A token-budgeted
batch makes the request size a constant the operator chose.

**A cache write happens per batch, not at the end.** A run interrupted halfway
keeps what it paid for. At this corpus size that is worth little; at a size
where it mattered, adding it afterwards would mean a schema change.

## Consequences

- `CREATE EXTENSION vector` lands in a migration rather than being assumed. The
  compose image (`pgvector/pgvector:pg16`) ships the extension but does not
  enable it in the database, and the failure if it is missing is a syntax error
  on the column type, which reads like a typo.
- The three strategies share the cache. Embedding all three costs the union of
  their `content_hash` sets, not three separate runs — and the structural and
  contextual strategies deliberately do not share a single hash, because their
  headers differ.
- The cache table holds no foreign key to any chunk. Chunks are not persisted
  anywhere yet, and a key to a table that does not exist is a comment with
  syntax. The `content_hash` is the join, and it is reproducible from the chunk
  at any time.
- A model change invalidates nothing automatically. The model id is a column and
  part of the lookup, so a different deployment simply misses and re-embeds; the
  old rows stay until someone prunes them, exactly as with the layout cache.
- The acceptance criterion — zero calls on a clean re-run — is checkable from
  the report the run prints, and is asserted in a test that runs the embed step
  twice against a stub.
