# Chunking

Three strategies split every parsed document into the units the retriever will
index. They exist to be compared: story 3.3 runs the same queries against all
three and attributes each difference to one property, so every parameter they do
not vary is held equal on purpose.

| Strategy | What it does |
| --- | --- |
| `fixed` | Slides a token-budgeted window over `content`. Ignores headings, tables and lists entirely. |
| `structural` | Packs blocks under their heading up to a token target. Tables are their own chunks; ordered step lists are never split. |
| `contextual` | The structural chunks, each with a breadcrumb prepended *for embedding only*. |

`contextual` is implemented as a wrapper around `structural`, not a copy, so the
two cannot drift: they emit identical `(start, end)` pairs and identical `text`.
The only difference between them is `context_prefix`, which is what the A/B is
measuring. If they cut in different places the experiment would have two
variables and neither could be attributed.

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

**The window is 220 tokens, not the conventional 512.** The median document is
183 tokens. At 512 a document would have to be longer than the p90 before it
split at all, so 108 of 120 documents would be a single chunk under every
strategy and the three-way comparison would be measuring nothing. 220 sits just
above the median document and just below the p75: it splits the long documents
and leaves the short ones whole.

**The fixed window uses the same 220 tokens the structural chunker targets.**
Different sizes would confound chunk size with boundary placement, and the
result would say nothing about structure.

**Overlap is 40 tokens, about 18% of the window.** Enough that a sentence
straddling a boundary survives whole in one of the two windows, small enough
that the corpus does not inflate by a fifth. Only `fixed` overlaps; the
structural strategies cut on boundaries that already exist.

**A single characters-per-token ratio would have been wrong.** Hungarian is
agglutinative and `cl100k_base` was trained overwhelmingly on English, so the
same character budget buys 1.9x the tokens. With one global ratio every
Hungarian chunk would run to nearly twice the budget the chunker believed it had
set. `HeuristicCounter` holds one ratio per language.

Boundaries are always decided by that heuristic, never by `tiktoken`. `tiktoken`
downloads its BPE table over HTTPS the first time it is asked for an encoding,
and a chunk boundary that depends on whether a download succeeded is not a
boundary. The heuristic is held to within 15% of the real tokenizer at the
median by a committed fixture
(`tests/ingest/chunking/fixtures/token_counts.json`).

## The two rules that override the size target

Both from ADR 0006, and both make the size distribution bimodal on purpose.

1. **A table is its own chunk.** An interval table answers a different question
   from the prose around it, and a row separated from its column headers is
   noise.
2. **An ordered step list is never split**, however far it overruns the target.
   Half a procedure reads exactly like a whole one, which is the failure mode
   that matters in a service manual. Bullet lists are deliberately *not*
   protected: a bullet list is a set of independent statements, so splitting one
   costs a little context rather than changing what it says.

The baseline breaks both, on purpose. Story 3.3 has to be able to attribute a
retrieval failure to exactly these rules, so the baseline has to actually commit
the error. A baseline that quietly respected structure would make the comparison
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

The same chunks, measured with the real tokenizer instead. Boundaries are
unchanged; only the measurement differs.

```
just chunk-stats --all --exact
```

| Strategy | Docs | Chunks | Chunks/doc | Min | Median | p90 | Max | Table chunks | Split step lists |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 120 | 227 | 1.89 | 38 | 186 | 229 | 329 | 0 | 25 |
| structural | 120 | 692 | 5.77 | 2 | 39 | 101 | 695 | 29 | 0 |
| contextual | 120 | 692 | 5.77 | 2 | 39 | 101 | 695 | 29 | 0 |

`--all` reads the layout cache for the 25 converted documents and fails rather
than analysing an uncached one; chunking is not where a bill should appear. It
needs `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and `AZURE_STORAGE_BLOB_ENDPOINT`.
Without them, `just chunk-stats` covers the 95 Markdown documents offline.

**How to read this.** `split_step_lists` is the headline: 0 for both structural
strategies and 25 for the baseline, which is the rule working and the contrast
story 3.3 needs. The structural median of 40 tokens against the baseline's 212
is not a defect. It is the atomicity rules and the heading boundaries producing
many short section chunks alongside a few long protected ones, which is exactly
the bimodality an average would have concealed. The max of 710 is a step list
that refused to split.

The heuristic and the exact counter disagree by a few percent at the median and
by more at the extremes, in the direction the 15% bound predicts.

## What story 3.3 compares

- `fixed` against `structural` isolates **boundary placement**: same target
  size, different notion of where a chunk ends.
- `structural` against `contextual` isolates **the header alone**: identical
  boundaries, identical text, one extra string in `embed_text`.

`content_hash` is the SHA-256 of `embed_text`, not of `text`, so the structural
and contextual chunks of the same slice have different embedding cache keys
(ADR 0006). Hashing `text` would have given them one cached vector between them
and scored the contextual strategy on the structural one's embeddings, with
every resulting number looking entirely plausible.

## Known gaps

- **Document Intelligence emits HTML tables where the Markdown parser emits
  pipes.** Both are chunked correctly, because the chunkers split on the block
  role and never read the text shape, but the two dialects reach the embedding
  as different strings. A table's content is therefore not like-for-like between
  a Markdown document and its converted twin. The `Table chunks` column counts
  both dialects. Normalising them is an ADR 0006 decision that has not been
  taken; until it is, a table comparison across the two routes is measuring the
  renderer as well as the strategy. Exactly one document in the corpus is
  affected, because the PDF renderer writes tables as spaced text and only the
  DOCX route produces a table at all.
- **Chunks are not persisted.** They are produced, measured and discarded. Where
  they live is a retrieval-story decision, and inventing a table now would be
  guessing at what the index wants.
- **`split_step_lists` detects a cut list by its first step not being step 1.**
  A chunk that ends partway through a list but starts at step 1 is not counted.
  Tightening it needs the list's true extent, which means a list-extent pass the
  chunkers do not currently share.
- **The heuristic ratios are corpus-specific.** A corpus in a third language
  needs its own ratio; the default would otherwise size its chunks as if it were
  English. `just chunk-stats --calibrate` cross-checks the committed constants
  against live chunks. It measures chunks where the constants are per-document,
  so the numbers land near each other rather than matching.
