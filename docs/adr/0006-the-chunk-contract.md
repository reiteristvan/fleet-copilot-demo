# 6. The chunk contract, and where a chunk's metadata comes from

Date: 2026-09-17

## Status

Accepted

## Context

A chunk is the unit retrieval indexes, agents cite and evals assert against.
Whatever shape it takes on the first day is the shape three stages bind to, and
changing it later means reindexing and rewriting every eval — the same argument
ADR 0003 made about the corpus front matter, one stage further down.

Three strategies will produce chunks from the same documents and be compared in
story 3.3: a fixed-size window with overlap, a heading-aware structural
splitter, and that splitter with a contextual header prepended before embedding.
A comparison across them is only meaningful if a chunk means the same thing in
all three.

Each chunk is required to carry `doc_id`, `type`, `machine_types`,
`item_numbers`, `language`, `revision`, `effective_date`, `section_path`,
`chunk_index` and `content_hash`. Seven of those are corpus front matter. The
problem is that front matter does not survive rendering: decompressing the text
streams of `data/corpus/published/sdm-43-service-manual.pdf` finds the prose and
none of the YAML keys. Document Intelligence never sees them, so a chunk built
from a PDF cannot recover its own machine types or effective date from what was
parsed. The metadata has to travel *beside* the document.

## Decision

**A chunk carries all ten fields, and `chunk_index` replaces `ordinal`.** The
name is the story's; renaming it now costs a rename and later costs a
migration, because `chunk_id` is derived from it.

**`content_hash` is the SHA-256 of `embed_text`, not of `text`.** It is the
embedding cache key, and strategies 2 and 3 differ *only* by the contextual
header — same slice of the same document, different string sent to the model.
Hashing `text` would give the two identical keys, and strategy 3 would silently
be scored on strategy 2's embeddings. The entire comparison would be invalid and
every number in it would look plausible.

**Metadata travels in `data/manifest.json` and in blob metadata, not in the
document.** `ManifestEntry` gains `machine_types`, `item_numbers`, `revision`
and `effective_date`; `MANIFEST_VERSION` goes to 2 so a reader can tell an old
manifest from a corrupt one; `corpus/upload.py` writes the same four as blob
metadata. All four are ASCII — lists comma-joined, the date ISO-8601, the
revision an integer — and well inside the 8 KB blob metadata limit.

The rejected alternative was reading the front matter from
`data/corpus/markdown/<doc_id>.md`, which exists on disk for all 120 documents
including the 25 that are published as PDF or DOCX. It works today and needs no
contract change. It was rejected because ADR 0003 deliberately does not upload
those Markdown copies: an ingest running against the container alone — which is
what a deployed one does — could not reproduce the metadata, and the difference
would not surface until the pipeline ran somewhere other than a laptop.

Rendering the front matter *into* the PDF was rejected for a sharper reason:
ADR 0003 keeps planted-case flags out of documents precisely so injection and
conflict evals cannot be solved by a metadata filter. Putting indexable metadata
back into the body walks into the same trap from the other side.

**Strategies 2 and 3 differ by exactly one field.** Strategy 2 leaves
`context_prefix` unset, strategy 3 sets it to
`<doc title> > <section path> | <machine types> | <item numbers>`. Anything else
held constant between them, so the comparison measures the header and nothing
else.

**A table is its own chunk, serialised as Markdown, and is never merged with
surrounding prose.** An interval table answers a different question from the
paragraph above it, and a fixed window that splits one mid-row produces rows
whose column headers are in a different chunk.

**A procedure step list is never split.** A step list is atomic even when it
exceeds the target chunk size: half a procedure is worse than no procedure,
because it reads as a whole one.

**Embeddings are cached in Postgres, keyed by `content_hash`.** The corpus is
deterministic and the chunkers are pure, so a re-run with no content change must
make zero embedding calls — that is the story's acceptance criterion, and it is
only checkable if the cache key is the thing that was actually embedded.

## Consequences

- `data/manifest.json` is regenerated with four new keys. Document bytes and
  their hashes do not change, so `just corpus-check` still passes on the
  documents themselves; only the manifest differs.
- Retrieval and evals may bind to the four new manifest keys. Adding more later
  stays additive and cheap; removing one of these is not, and needs its own ADR.
- Chunk sizes will not be uniform. Table and step-list chunks are as long as
  they need to be, so the distribution `scripts/chunk_stats.py` reports is
  bimodal by construction, not by accident. A stats table showing otherwise
  means the atomicity rules are not being applied.
- The fixed-size baseline will split tables and step lists, because that is what
  a baseline is for. Story 3.3 should be able to attribute a retrieval failure
  to exactly that.
- Nothing here decides how big a chunk is. Target sizes and overlap are tuning,
  recorded in `docs/chunking.md` alongside the measurements that justify them,
  not in an ADR.
