# Chunking

Three strategies split every parsed document into the units the retriever will
index. They exist to be compared. Story 3.3 runs the same queries against all
three and attributes each difference to one property. Every parameter they do
not vary is held equal on purpose.

| Strategy | What it does |
| --- | --- |
| `fixed` | Slides a token-budgeted window over `content`. Ignores headings, tables and lists entirely. |
| `structural` | Packs blocks under their heading up to a token target. Tables are their own chunks; ordered step lists are never split. |
| `contextual` | The structural chunks, each with a breadcrumb prepended *for embedding only*. |

`contextual` wraps `structural` rather than copying it, so the two cannot drift.
They emit identical `(start, end)` pairs and identical `text`. The only
difference is `context_prefix`, which is what the A/B measures. If they cut in
different places the experiment would have two variables, and neither could be
attributed.

## The parameters, and why these numbers

Measured over all 120 Markdown sources with `cl100k_base`, front matter
excluded. Reproduce with `uv run python scripts/capture_token_counts.py`.

| Quantity | Value |
| --- | --- |
| Documents | 120 |
| Total tokens | 34,085 |
| Tokens per document, min / p25 / median / p75 / p90 / max | 84 / 168 / 183 / 403 / 466 / 1240 |
| Characters per token, English (n=107) | 4.17 |
| Characters per token, Hungarian (n=13) | **2.21** |

**The window is 220 tokens.** The conventional 512 would not work here. The
median document is 183 tokens. At 512 a document would have to exceed the p90
before it split at all. 108 of 120 documents would be one chunk under every
strategy, and the comparison would measure nothing. 220 sits above the median
document and below the p75. It splits the long documents and leaves the short
ones whole.

**The fixed window uses the same 220 tokens the structural chunker targets.**
Different sizes would confound chunk size with boundary placement. The result
would say nothing about structure.

**Overlap is 40 tokens, about 18% of the window.** A sentence that straddles a
boundary survives whole in one of the two windows. The corpus does not inflate
by a fifth. Only `fixed` overlaps. The structural strategies cut on boundaries
that already exist.

**A single characters-per-token ratio would have been wrong.** Hungarian is
agglutinative. `cl100k_base` was trained mostly on English. The same character
budget buys 1.9x the tokens. With one global ratio, every Hungarian chunk would
run to nearly twice its intended budget. `HeuristicCounter` holds one ratio per
language.

That heuristic decides every boundary. `tiktoken` never does. `tiktoken`
downloads its BPE table over HTTPS the first time it is asked for an encoding. A
boundary that depends on whether a download succeeded is not a boundary. A
committed fixture holds the heuristic to within 15% of the real tokenizer at the
median (`tests/ingest/chunking/fixtures/token_counts.json`).

## The two rules that override the size target

Both from ADR 0006, and both make the size distribution bimodal on purpose.

1. **A table is its own chunk.** An interval table answers a different question
   from the prose around it. A row separated from its column headers is noise.
2. **An ordered step list is never split**, however far it overruns the target.
   Half a procedure reads exactly like a whole one. That is the failure mode
   that matters in a service manual. Bullet lists are not protected. A bullet
   list is a set of independent statements, so splitting one costs a little
   context rather than changing what it says.

The baseline breaks both rules on purpose. Story 3.3 must be able to attribute a
retrieval failure to exactly these rules, so the baseline has to commit the
error. A baseline that quietly respected structure would make the comparison
flattering and useless.

## The distribution

All 120 documents. Sizes measured with the offline heuristic, the same counter
that chose the boundaries.

```
just chunk-stats --all
```

| Strategy | Docs | Chunks | Chunks/doc | Min | Median | p90 | Max | Table chunks | Split step lists |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 120 | 227 | 1.89 | 42 | 212 | 220 | 220 | 0 | 25 |
| structural | 120 | 692 | 5.77 | 3 | 40 | 108 | 710 | 29 | 0 |
| contextual | 120 | 692 | 5.77 | 3 | 40 | 108 | 710 | 29 | 0 |

The same chunks, measured with the real tokenizer. The boundaries are
unchanged. Only the measurement differs.

```
just chunk-stats --all --exact
```

| Strategy | Docs | Chunks | Chunks/doc | Min | Median | p90 | Max | Table chunks | Split step lists |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 120 | 227 | 1.89 | 38 | 186 | 229 | 329 | 0 | 25 |
| structural | 120 | 692 | 5.77 | 2 | 39 | 101 | 695 | 29 | 0 |
| contextual | 120 | 692 | 5.77 | 2 | 39 | 101 | 695 | 29 | 0 |

`--all` reads the layout cache for the 25 converted documents. It fails rather
than analysing an uncached one, because chunking is not where a bill should
appear. It needs `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and
`AZURE_STORAGE_BLOB_ENDPOINT`. Without them, `just chunk-stats` covers the 95
Markdown documents offline.

**How to read this.** `split_step_lists` is the headline. It is 0 for both
structural strategies and 25 for the baseline. That is the rule working, and it
is the contrast story 3.3 needs.

The structural median of 40 tokens against the baseline's 212 is not a defect.
The atomicity rules and the heading boundaries produce many short section chunks
beside a few long protected ones. An average would have concealed that. The max
of 710 is a step list that refused to split.

The heuristic and the exact counter disagree by a few percent at the median, and
by more at the extremes. That is the direction the 15% bound predicts.

## Embedding

Every chunk goes to `text-embedding-3-large` at its native 3072 dimensions. The
vector is cached in Postgres by `content_hash`, the SHA-256 of `embed_text`.
That is what is actually sent (ADR 0007).

```
just embed-corpus            # report what would be sent
just embed-corpus --apply    # send it
```

| Strategy | Chunks | Distinct | Tokens | Requests |
| --- | ---: | ---: | ---: | ---: |
| fixed | 227 | 227 | 39,581 | 6 |
| structural | 692 | 560 | 30,642 | 4 |
| contextual | 692 | 689 | 57,110 | 7 |
| **union** | 1,611 | **1,476** | **127,333** | 17 |

At $0.13 per million tokens that is **$0.017** for the whole corpus across all
three strategies. That number does not justify the cache. The acceptance
criterion does: a clean re-run must make zero calls. That is a statement about
determinism. Three strategies are about to be compared, and the inputs must not
move between runs.

**The second `--apply` embeds nothing.** That is the externally visible proof
that `content_hash` describes what was actually sent:

```
fixed        227 chunks, 227 distinct, 227 already cached, embedded 0 in 0 requests
structural   692 chunks, 560 distinct, 560 already cached, embedded 0 in 0 requests
contextual   692 chunks, 689 distinct, 689 already cached, embedded 0 in 0 requests
```

**Two numbers worth reading.** `structural` collapses 692 chunks into 560
distinct vectors. 132 chunks repeat text across documents, such as shared safety
boilerplate and identical headings. The cache pays for each once.

The three strategies share nothing. 227 + 560 + 689 is exactly the 1,476 rows in
the table. `fixed` cuts in different places. `contextual` prepends a header to
every chunk. So no hash appears under two strategies. That is the design
working. It lets story 3.3 score each strategy on its own vectors.

Batches are bounded by tokens, not by a count. Azure accepts up to 2048 inputs
per request, but the throttle is tokens per minute. Chunks here run from 40 to
over 1,000 tokens, so a fixed-count batch would send an unpredictable amount.
The request that finally trips the limit would have no visible cause. The budget
is 8,000 tokens against a 50,000-per-minute deployment, four requests in
flight.

## What story 3.3 compares

- `fixed` against `structural` isolates **boundary placement**. Same target
  size, different notion of where a chunk ends.
- `structural` against `contextual` isolates **the header alone**. Identical
  boundaries, identical text, one extra string in `embed_text`.

`content_hash` is the SHA-256 of `embed_text`, not of `text`. So the structural
and contextual chunks of one slice have different cache keys (ADR 0006). Hashing
`text` would give them one cached vector between them. The contextual strategy
would then be scored on the structural one's embeddings, and every resulting
number would look plausible.

## Known gaps

- **Document Intelligence emits HTML tables where the Markdown parser emits
  pipes.** Both are chunked correctly, because the chunkers split on the block
  role and never read the text shape. The two dialects still reach the embedding
  as different strings, so a table's content is not like-for-like between a
  Markdown document and its converted twin. The `Table chunks` column counts
  both dialects. Normalising them is an ADR 0006 decision that has not been
  taken. Until it is, a table comparison across the two routes measures the
  renderer as well as the strategy. One document is affected, because the PDF
  renderer writes tables as spaced text and only the DOCX route produces a table.
- **Chunks are not persisted.** They are produced, measured and discarded. Where
  they live is a retrieval-story decision. Inventing a table now would guess at
  what the index wants.
- **`split_step_lists` detects a cut list by its first step not being step 1.**
  A chunk that ends partway through a list but starts at step 1 is not counted.
  Tightening it needs the list's true extent, which means a list-extent pass the
  chunkers do not share.
- **The heuristic ratios are corpus-specific.** A corpus in a third language
  needs its own ratio. The default would size its chunks as if it were English.
  `just chunk-stats --calibrate` cross-checks the committed constants against
  live chunks. It measures chunks where the constants are per-document, so the
  numbers land near each other rather than matching.
