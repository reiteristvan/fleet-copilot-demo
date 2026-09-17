# 5. Layout parsing through Document Intelligence, and the cache in front of it

Date: 2026-09-17

## Status

Accepted

## Context

`ingest` currently reads `*.txt` off the disk. The corpus it actually has to
read is 120 documents, of which 25 are published as PDF or DOCX and 95 stay
Markdown (ADR 0003). Five of the twenty PDFs are image-only: no text layer at
all, one of them carrying a planted prompt injection that is reachable by no
other route. Markdown needs no parser. The other two formats do, and for five
files that parser has to be an OCR engine.

Downstream, four chunking strategies want to run over the same documents and be
compared: a fixed token window, a layout-aware splitter, a hierarchical
parent-child splitter, and a semantic one. A comparison is only meaningful if
every strategy reads *identical* input. A parser that is re-run per strategy
does not guarantee that, and a service that is re-run at all introduces a
variable the comparison is supposed to hold still.

There is also a cost question, though a smaller one than it looks. The story
that requested this work estimated 25 files of 15 pages each, ~400 pages, ~$4
per full parse against `prebuilt-layout`. The corpus on disk is 20 single-page
PDFs and 5 DOCX files: one full parse is roughly 30 billable units, about $0.30.
Cost is not what justifies a cache here. Reproducibility is.

## Decision

**`prebuilt-layout`, not `prebuilt-read`.** Read is OCR only, at roughly
$1.50/1,000 pages against layout's $10. The extra $8.50 buys headings,
paragraph roles, reading order and table structure — which is the entire input
to three of the four chunking strategies. Buying Read would mean reconstructing
document structure by counting `#` characters, which is what a framework default
does and what returns nothing at all on the five scanned files, where there are
no `#` characters to count.

**A parser is a `Protocol` with two implementations, and the local one raises
rather than degrades.** `AzureLayoutParser` calls the service; `PyMuPdfParser`
reads a text layer and is what unit tests and Markdown-only runs use, so CI
never touches Azure. `PyMuPdfParser` raises on a PDF with no text layer instead
of returning an empty document. A silent empty return would make the suite green
while the OCR path — and the injection planted behind it — went untested, which
is the failure this project can least afford to ship.

**A parsed document is one `content` string plus blocks that are spans into
it.** Nothing else holds text. Every chunker therefore slices one coordinate
system, and `Chunk`'s span invariant is satisfiable by construction rather than
re-derived, differently, four times.

**Page headers, footers and page numbers stay in `content`, tagged by role.**
Stripping them is the obvious move and it is wrong: it shifts every offset after
the strip, so no span can be checked against the cached JSON any more. Tagging
leaves the decision to each chunker and leaves it measurable.

**The cache stores the raw `AnalyzeResult` JSON, not our model of it**, in a
`layout-cache` Blob container under
`{model_id}/{api_version}/{source_sha256}.json`. The parsed model is
derived on read. Changing how we interpret a layout — adding a role, changing
how a table serialises — then costs nothing, while re-analysing costs money and
a round trip. Model id and API version sit in the key so switching either one
invalidates cleanly instead of needing a manual purge.

**`Chunk` gains `context_prefix`; the span invariant is not relaxed.** A chunk's
subject often appears only in the heading above it, outside its own span.
`embed_text` returns the breadcrumb joined to the text and is what the retriever
embeds; `text` and `start`/`end` stay verbatim and are what a citation quotes.
Relaxing the invariant instead would let a citation highlight the wrong passage,
which is invisible until a human reads it — the exact failure the validator was
written to prevent.

**Both parsing dependencies are dev-only.** Parsing is an offline operation run
from a developer machine or CI; the API image reads the cache and never analyses
anything. This also keeps PyMuPDF's AGPL-3.0 licence out of an MIT wheel.

## Consequences

- The Document Intelligence account is a new Azure resource: S0 (F0 truncates
  every request to 2 pages and caps files at 4 MB, which is unusable even here),
  `kind: 'FormRecognizer'` because the ARM kind kept the pre-rename name,
  `disableLocalAuth: true`, `customSubDomainName` set so token auth works. The
  user-assigned identity gets Cognitive Services User on it. A second
  subscription-scoped budget alerts at $15 on this resource alone.
- Three `AnalyzeResult` fixtures — one text PDF, one scanned PDF, one DOCX —
  are captured once from the live service and committed. Every mapper test runs
  against them, so the mapping is tested without an Azure account and without
  spending anything.
- `PyMuPdfParser` is a strictly weaker path, not an equivalent one. It produces
  no heading roles and no tables, so a chunking comparison run against it
  measures the fallback, not the pipeline. Tests assert what it cannot do.
- A corpus regeneration changes content hashes and orphans the cached JSON under
  the old keys. Orphans are harmless — they are never read — but the container
  grows by one full corpus per regeneration until someone prunes it.
- Nothing in this ADR decides how documents are chunked. It decides what the
  chunkers read, and fixes it so that four of them can be compared on equal
  terms.
