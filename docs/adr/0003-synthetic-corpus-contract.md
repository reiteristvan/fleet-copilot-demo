# 3. A deterministic synthetic corpus, and the contract it publishes

Date: 2026-09-16

## Status

Accepted

## Context

Every stage downstream of `ingest` needs documents to work on, and the ones a
cleaning-machine manufacturer actually holds — operator manuals, service
records, shift handovers — are copyrighted, commercially sensitive, or both.
Real documents also make poor test material for a second reason: we cannot
plant anything in them. A retrieval pipeline is only measurably correct if the
corpus contains cases whose right answer is known in advance.

So the corpus is synthetic. The question is what generates it.

The obvious answer is the chat model this project already deploys. It writes
convincing prose, and its incidental vocabulary reuse is exactly what makes
retrieval non-trivial. It also makes the corpus unreproducible: two runs
produce two different corpora, so a metric moving between runs cannot be
attributed to a pipeline change rather than to the documents shifting
underneath it. It costs money per regeneration, it needs a data-plane role
assignment on every developer's principal, and at the deployed 30k TPM it is
the slowest step in the repository by an order of magnitude.

The second question is what the corpus *publishes*. Retrieval will filter on
its metadata, agents will cite it, and evals will assert against it. Whatever
shape the front matter takes on the first day is the shape three stages bind
to, and changing it later means reindexing and rewriting every eval.

## Decision

**The corpus is generated offline and deterministically.** A seed declared in
`data/corpus_spec.yaml` drives an explicitly-threaded `random.Random`; prose is
assembled from fragment banks in `data/fragments/`, and every document draws its
nouns — machine types, item numbers, error codes, wear limits, serials, sites —
from the single catalogue in `data/catalogue.yaml`. Lexical overlap between
documents is therefore produced on purpose rather than hoped for.

Determinism extends to the binary formats: PDF creation dates and producer
strings are pinned, and DOCX zip entry timestamps are normalised. A test
generates the whole corpus twice and asserts every SHA-256 matches. Without
that, `data/manifest.json` records hashes that change on every run and is worse
than no manifest at all.

**The document contract is YAML front matter over Markdown**, with these keys
and no others: `doc_id`, `type`, `machine_types`, `item_numbers`, `serials`,
`site`, `language`, `revision`, `effective_date`.

**Planted cases are registered in the manifest, never in the document.** Each
manifest entry carries `planted`, `planted_kind`, `planted_id` and a note. A
`planted: true` flag in a document's front matter would be indexed along with
everything else, and every injection and conflict eval would then be solvable
by a metadata filter — the test would pass while testing nothing.

**A document that is converted is represented by its converted format only.**
The 20 PDFs and 5 DOCX files replace their Markdown in the uploaded set rather
than accompanying it. Uploading both would plant an exact-duplicate pair nobody
intended, and near-duplicate handling is a property we want to measure, not one
we want to accidentally supply.

## Consequences

- Regenerating the corpus is free, offline, and produces byte-identical output,
  so the manifest is meaningful and CI can verify it. No Azure role assignment
  is needed to work on any stage of the pipeline.
- Prose variety is bounded by the fragment banks. Documents of one type share
  more sentence skeleton than model-written text would. This is the real cost of
  the decision. It is mitigated by combinatorial assembly and wide banks, and it
  is acceptable because retrieval difficulty here comes from *vocabulary overlap
  across* documents, not from sentence variety within one.
- PDFs are generated from English documents only. The PDF core fonts do not
  carry `ő` or `ű`, and bundling a Unicode TTF would add a font licence to the
  repository for no measurable benefit. Hungarian is represented in Markdown and
  DOCX instead, so the corpus still spans three formats and two languages.
- Five of the twenty PDFs are image-only, with no text layer. They exist so that
  OCR is a path under test rather than a pass-through, and one of the planted
  prompt injections is reachable only through it.
- Adding a front-matter key later is cheap; changing or removing one is not,
  because retrieval filters and eval assertions bind to it. New optional keys
  are additive and do not need a new ADR. A change to an existing key does.
- The generator lives under `src/fleet_copilot/corpus/` rather than in
  `scripts/`, so `mypy --strict` and `pytest` cover it. `scripts/gen_corpus.py`
  and `scripts/upload_corpus.py` are argument-parsing shims with no logic, since
  `[tool.mypy] files` does not include `scripts`.
