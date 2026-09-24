# Engineering journal

Newest first. Short entries: what changed, what surprised us, what is still
open. Decisions that outlive a session graduate to an ADR in `docs/adr/`.

## 2026-09-24 — Embeddings, revision precedence, and four decisions

Plan 3 is done. Every chunk now has a vector from `text-embedding-3-large` at
3072 dimensions, cached in Postgres by `content_hash`. The acceptance criterion
holds: a second run embeds nothing.

```
fixed        227 chunks, 227 distinct, 227 already cached, embedded 0 in 0 requests
structural   692 chunks, 560 distinct, 560 already cached, embedded 0 in 0 requests
contextual   692 chunks, 689 distinct, 689 already cached, embedded 0 in 0 requests
```

1,476 distinct vectors over 127,333 tokens. That cost $0.017.

Two numbers are worth reading. `structural` collapses 692 chunks into 560
vectors, because 132 chunks repeat text across documents. The three strategies
share nothing: 227 + 560 + 689 is exactly the row count. `fixed` cuts elsewhere,
and `contextual` prepends a header to every chunk, so no hash appears twice.
That is the design working. Story 3.3 scores each strategy on its own vectors.

**A 401 from Azure OpenAI stopped the run.** The developer principal held
Cognitive Services User. That role covers Document Intelligence. The OpenAI data
plane gates on Cognitive Services OpenAI User, and only the managed identity had
it. This is the third time the same shape has cost a run. Story 1.2 hit it twice.
Neither error names a role. The first said the principal lacks a data action. The
second said only "no access".

**psycopg's async driver cannot run on Windows.** It refuses the default
ProactorEventLoop and raises `InterfaceError`. The embedding store now uses the
synchronous driver through `asyncio.to_thread`, which is what `ingest/cache.py`
already does.

Then the same driver turned up a live bug. `api/health.py` still used
`AsyncConnection`, and its `except Exception` is deliberately broad so that a
health endpoint reports a failure instead of becoming one. So `/healthz`
reported the database as down on every Windows host, healthy or not. The Linux
container was fine, which is why nobody saw it.

Worse, every database assertion in `test_health.py` was about a failure. A check
that could never succeed passed the suite. That is the same shape as the PDF
renderer in the entry below: the tests proved one property and said nothing about
correctness. Verified against the live database before and after:

```
OLD AsyncConnection: InterfaceError
NEW sync+to_thread: ok, pgvector 0.8.6
```

**`is_current` cannot be derived from document metadata.** The index needs to
know which documents are superseded, so the planted conflict resolves to revision
4 rather than revision 3. The manifest gives every document a revision integer
and nothing that groups documents into a series.

Grouping by `(type, machine_types, item_numbers)` looked right and was measured
instead of assumed. It forms 29 groups and marks 68 of 120 documents superseded.
One group holds eight handover notes for the same machine and part: separate
shift events, written on different days at different sites, every one revision 1.
It used document topic as evidence of document lineage.

The rule now reads the `-rev<N>` suffix on the doc_id and nothing else. That
demotes exactly one document, which is the one the planted case is about. ADR
0009 records it.

### Four decisions

Four things had working resolutions but no agreement. All four are now decided.

**Data-plane roles.** `just preflight` probes all five planes and names the role
a failure needs. Every probe makes a real call. A role listing would have lied:
when the OpenAI assignment was finally made, the call kept failing for minutes
while it propagated.

Writing it caught a fifth gap. Listing Search index definitions passes under
Subscription Owner, because Owner carries `Microsoft.Search/*`. Reading and
writing documents are data actions that Owner does not carry. One check would
have reported Search reachable until the first `upload_documents` 403. Search is
now probed twice, once per grant.

**Async Postgres.** Sync plus `to_thread` everywhere. The alternative was
`AsyncConnection` plus a Windows event-loop policy. That trades a local bug for a
process-wide constraint, because `SelectorEventLoop` cannot run subprocesses on
Windows, and it buys nothing until a connection pool exists.

**Tests that commit.** They isolate by key, not by cleanup. The store tests use a
`test-embeddings` model id. A teardown delete runs only if the test got that far.
A key that cannot match a real run is safe even when the test dies halfway.
Written against the live name once, this left four fake vectors beside 1,476 real
ones. A row count that failed to add up was the only sign.

A transactional fixture was rejected. It would invert who owns the commit, which
ADR 0007 decided on purpose. A separate test database was rejected too. It
isolates by making the tests stop touching the database the application uses.

**Plan documents.** Plans specify behaviour, not literal test code. Five
specified tests across plans 2 and 3 could not run at all. The one that cost real
time was `section_path_at`: the plan supplied an implementation and a test that
agreed with it, and both were wrong. Every structural chunk reported the
breadcrumb of the section above its own text. The test used offset 0 of a
document that starts with its title, where "before any heading" and "at the first
heading" are the same position, so it asserted the bug.

A wrong snippet costs a debug cycle. A wrong specified test steers the
implementation and then confirms it.

### Also

An endpoint hostname had been committed to this public repository as an example
value. History was rewritten and force-pushed, which stops it spreading and
retracts nothing. So the account was rotated and deleted, and the hostname no
longer resolves. ADR 0008 records the naming salt that made rotation possible
without renaming the whole environment.

The layout cache survived that swap untouched. It keys on
`(source_sha256, model_id, api_version)` and not on the endpoint, so all 25
cached layouts stayed valid. Keyed by endpoint, a rotation would have cost a full
re-analysis.

Open: chunks are still not persisted anywhere. The two table dialects are still
unnormalised. Docker Desktop stopped by itself twice during this session, which
turns 473 passing tests into 438 passed and 35 skipped. A green run with skips
looks much like a green run.

## 2026-09-22 — Three chunking strategies, and an endpoint that had to be retired

Three strategies now chunk the same 120 documents. A fixed window is the
baseline. A structural chunker reads headings. A contextual chunker wraps the
structural one and adds a header.

`contextual` wraps rather than copies. The two must cut in the same places. If
they did not, story 3.3 could not tell a header effect from a boundary effect.
`docs/chunking.md` has the numbers. ADR 0006 has the contract.

The parameters came from the corpus, not from convention. The median document is
183 tokens. A 512-token window would leave 108 of 120 documents as one chunk, and
the comparison would measure nothing. 220 splits the long documents and leaves
the short ones whole.

The characters-per-token ratio is per language because the measurement required
it. English is 4.17. Hungarian is 2.21. One global ratio would let every
Hungarian chunk run to nearly twice its budget.

**Every structural chunk reported the wrong section, and its test agreed.**
`section_path_at` stopped at the first block at or after the offset. A heading
sitting exactly on the offset was excluded. The structural chunker flushes on a
heading, so every chunk it emits begins at one. Each chunk got the breadcrumb of
the section above its own text. A chunk opening `## Safety` reported `Parts and
consumables`.

The module's own test asserted the off-by-one. It used offset 0 of a document
that starts with its title. At that position "before any heading" and "at the
first heading" are the same thing. A different module's test found the bug. A
test whose fixture cannot separate the two cases it arbitrates is evidence about
neither.

Two smaller defects, both in tests. `zip(chunks, chunks[1:], strict=True)` always
raises, because the slice is one shorter. That is the point of the idiom.
`strict=True` had been added to satisfy a lint rule. `itertools.pairwise` says it
properly.

The `--calibrate` flag printed the aggregate characters-per-token. The constant
it claimed to regenerate is a per-document median. Following its own instructions
would have moved the constant from 4.17 to 4.29. It now names both statistics.

**The HTML-versus-pipe table disagreement returned as a metric.** The entry below
records that Document Intelligence emits `<table>` where the Markdown parser
emits pipe rows. The chunkers were never affected, because they split on the
block role and never read the text shape. The stats column counted chunks
starting with `|`, so it reported the 25 converted documents as having no tables.
That is the one construct ADR 0006 gives its own chunk type.

Counting both dialects fixes the number. Normalising them is still undecided, and
it still blocks a like-for-like table comparison across the two routes. One
document is affected. The PDF renderer writes tables as spaced text, so only the
DOCX route produces a table at all.

**A planning document carried the real Document Intelligence endpoint as an
"e.g." value, in a public repository.** It is not a credential, because every
data plane here is keyless. It names one specific deployment. The privacy pass in
September removed the subscription and tenant ids for that reason and walked past
this.

History was rewritten and force-pushed. That stops the value spreading and
retracts nothing: the old commits stay reachable by SHA until GitHub collects
them. So the account was rotated and deleted, and the hostname no longer
resolves.

Every name in `main.bicep` derives from the subscription id and the environment
name. That keeps a redeploy idempotent. It also would have handed the account its
old name straight back after a delete. Document Intelligence now carries its own
rotation salt. ADR 0008 records it.

The cache made the rotation nearly free, by an accident worth keeping. Layouts
key on `(source_sha256, model_id, api_version)` and not on the endpoint. All 25
survived the swap, and `just corpus-parse` still reports "would analyse 0, 25
already cached". Keyed by endpoint, a rotation would have cost a full
re-analysis. Nobody made that argument when the key was chosen.

Open: the two table dialects are still unnormalised. Chunks are produced,
measured and discarded, because where they live is a retrieval-story decision.
The unreachable commits stay on GitHub until support collects them.

The value got into the repository as an example in prose. Nothing treats prose as
sensitive and no reviewer looks for it there. Write example endpoints as
placeholders from the start.

## 2026-09-18 — parsing, and three things the corpus was hiding

Added Azure AI Document Intelligence and the layout cache in front of it:
Markdown parsed natively, PDF and DOCX through `prebuilt-layout`, all three
producing the same `ParsedDocument` so a chunker cannot tell which route a
document took. ADR 0005 and ADR 0006 carry the decisions.

Three things turned up that had nothing to do with the feature.

**The PDFs had been shredded since story 1.1, and every test passed.** fpdf2
defaults `multi_cell`'s `new_x` to `XPos.RIGHT`, and the renderer assumed the
cursor returned to the left margin. Every other line started at the right
margin and ran off the page. The service manual reached the service as 699
characters of fragments — `- The emer` / `isolator is h` where the safety
bullet should be — and the two manuals kept 32% and 35% of their text. The
corpus suite was green throughout, because the output was still a valid PDF and
still byte-identical between runs. Determinism tests prove a generator is
repeatable. They say nothing about whether it is right, and every assertion we
had was about repeatability.

It only surfaced because `prebuilt-layout` returned no tables for a document
that has one, and that was worth not explaining away. The fix is one argument
per call; after it every PDF keeps 100%+ of its source and the manual comes back
with a title and five section headings. A fixture assertion now pins the
character count, so a silent return to 699 fails as a renderer regression rather
than passing as a parser that found less.

**Subscription Owner grants no data-plane access.** `upload_corpus.py` has said
so in its docstring since it was written, and the developer principal still had
only `Owner` — so `just corpus-upload --apply` would have 403ed too, and nobody
had run it. The Bicep now takes a `developerPrincipalId` and grants Cognitive
Services User and Storage Blob Data Contributor, with `principalType` as a
parameter so a team can point it at a group instead. A docstring that predicts a
failure is not a mitigation.

**In Markdown mode the service still emits tables as HTML.** `<table><tr><th>`,
not pipe rows — while the Markdown parser emits pipes, straight from the source.
So the two parsers agree on everything except the one construct ADR 0006 gives
its own chunk type. Recorded as a test rather than patched over, because plan 2
has to normalise it or the strategy comparison stops being like-for-like.

Smaller corrections, all from the plan being written before the code ran:

- **`azure.identity.aio` needs an async transport.** The module imports fine
  without `aiohttp` and fails at construction, so nothing caught it until the
  credential was built. `azure-core[aio]` is a main dependency, not a dev one:
  `get_async_credential()` is part of the package surface.
- **`what-if` reports a role assignment as *unsupported*, not as a create** — its
  id depends on a runtime `reference()`. The ten modifies it lists are
  pre-existing noise about undeclared read-only properties. Verified by running
  what-if against the unmodified template and diffing the counts, which is the
  only way to read that output honestly.
- **`git stash` corrupts `deploy.sh` on Windows.** It holds one intentional bare
  CR in `tr -d`, and the round-trip normalises it to LF, which would collapse
  eleven deployment outputs onto one line. `pathlib.write_text` translating `
`
  to `
` is how it got there.

Open: the PDF renderer writes a table as space-separated text rather than a
grid, so `prebuilt-layout` finds tables only in the DOCX. Chunking and embedding
are planned but not built.

## 2026-09-16 — The operational half: registry, states and telemetry

Forty machines across the corpus's nine sites, ninety days, 2.34M telemetry
samples at one-minute cadence, plus 100k state intervals, 787 fault events and
63 tickets. Generated from the same `data/catalogue.yaml` as the documents, so
a serial in a service report resolves to a machine and 46 fault events carry
the `doc_id` of the document describing them — read out of the corpus rather
than declared. [docs/data-model.md](data-model.md),
[ADR 0004](adr/0004-telemetry-storage-and-partitioning.md).

Four things worth keeping.

**`CROSS JOIN reporting.as_of` plans terribly.** The clock lives in a one-row
table so "last 7 days" means the same thing whenever it is asked. But the
planner cannot know what a one-row table holds, so the predicate became a join
filter applied *after* the rows were produced: all 3,640 daily rows
materialised, 3,360 thrown away, 330 ms to return 40. Two of the six reference
queries were over budget and nothing about the SQL looked wrong. The same value
behind a `STABLE` function can be used as an index qualifier — 332 ms to 1.8 ms.
It needs `SECURITY DEFINER` too: unlike a view, a function runs as the invoker,
so the first version was refused inside its own body.

**Half-open ranges are load-bearing, not stylistic.** With an inclusive upper
bound, two adjacent intervals share their boundary instant, the exclusion
constraint rejects the pair, and the machine can never change state at all.

**The hour grid has to come from the state intervals, not the samples.** A
switched-off machine emits no telemetry, so a sample-derived grid has no row for
those hours and "minutes off" reads as missing rather than zero — the difference
between a machine that was idle and a machine nobody can account for.

**`hash()` is not a stable hash.** Python randomises string hashing per process,
so using it to decide which machines run two shifts dealt the fleet a different
shape on every run. Caught before it shipped this time, unlike the DOCX
create-system byte last session; same class of bug, which is "reproducible on
this machine" mistaken for "reproducible".

Tuning the behaviour model was not decoration: it was making the reference
queries able to discriminate. The first pass had 22 of 40 machines
deep-discharging daily, because a 30-minute shift handover cannot recharge a
pack — so "machines finishing flat" would have been a question about the shift
pattern rather than about the machines. A realistic handover gap and a realistic
charge rate brought it to 6.

Still open: the database tests skip when PostgreSQL is unreachable, because CI
runs no PostgreSQL service and `.github/workflows/` needs a human go-ahead to
touch. 24 of the fleet tests run everywhere; 26 need `just up`. Adding a
service container to CI is the obvious fix and is a decision, not a diff.

## 2026-09-16 — A synthetic corpus with known defects in it

120 fleet documents generated offline from `data/`: a catalogue of five machine
families and their configuration variants, a spec declaring how many documents
of each type and language, and nine files of prose fragments the documents are
assembled from. 95 Markdown, 20 PDF, 5 DOCX; 107 English, 13 Hungarian.

The decision worth recording is **not** using the deployed chat model. It writes
better prose, and its incidental vocabulary reuse is exactly what makes
retrieval non-trivial. But it makes the corpus unreproducible, and an
unreproducible corpus means a metric that moves between runs cannot be
attributed to a pipeline change rather than to the documents shifting
underneath it. Offline generation costs prose variety and buys a committed
manifest of content hashes that is a fact rather than a claim.
[ADR 0003](adr/0003-synthetic-corpus-contract.md).

Determinism took one fix per format, and every one was found by rendering twice
across a real time gap rather than by reading the docs:

- **fpdf2** stamps the current time into `CreationDate` unless it is pinned.
- **python-docx** writes the current time into every zip entry header. Its
  member *contents* are already deterministic, so only the timestamps need
  normalising — and the first check passed by luck, because two renders
  milliseconds apart landed in the same second.
- **Pillow's PDF writer** stamps a creation date that cannot be pinned at all,
  so the scanned pages are drawn with Pillow and assembled with fpdf2.

One thing did ship broken, and it is the same shape as the three below:
**`zipfile.ZipInfo` takes its create-system byte from `sys.platform`** — 0 on
Windows, 3 on Unix. The DOCX normaliser pinned entry timestamps and inherited
that byte, so all five DOCX files hashed differently in CI and nowhere else.
Every determinism check passed locally, including two that render twice and
compare, because they all ran on one platform. Reproduced in a Linux container
in about five minutes; the lesson is that "deterministic" was only ever tested
against *time*, never against *platform*.

Three more nearly shipped broken, all of the same shape — correct locally,
wrong somewhere else:

- **`dist/` is in the stock Python `.gitignore`.** The 25 PDFs and DOCX files
  were written there, `git add data/corpus` skipped all of them without a word,
  and the working tree, the tests and the manifest were all still right. Only a
  fresh clone would have been missing two of the three formats. Found by hashing
  what git had *stored* rather than what was on disk; that check is now a test,
  and the directory is `published/`.
- **`.docx` was being handled as text.** Under `* text=auto eol=lf` git was
  deciding by content heuristic whether to rewrite line endings inside a zip
  whose SHA-256 the manifest records. `*.pdf binary` was already there; `*.docx`
  is now too.
- **fpdf2's core fonts are Latin-1, not cp1252**, so the em dash in every
  document title was unrenderable. Known typography is now transliterated and
  anything else outside Latin-1 raises — which means a Hungarian document routed
  to a PDF fails instead of silently losing its accents, in a corpus whose
  Hungarian exists to measure cross-language retrieval.

A fourth was a design mistake rather than a platform one. Placeholders a
document could not supply originally rendered as an em dash, which produced
"Empty the — litre hopper" in a scrubber-dryer manual: a document confidently
describing a part the machine does not have. Unfillable phrases are now never
selected, and because that makes a *misspelled* placeholder silently shrink a
bank instead of failing, the fragments are checked against the known
placeholder set at the gate.

Six documents are deliberately wrong — a pair of conflicting procedure
revisions, a shift note disputing the brush wear limit, and three indirect
prompt injections, one of them reachable only through OCR. None of them says so
in its own front matter. A `planted` flag would be indexed along with the text
and turn every one of these into something a metadata filter solves.

Still open: uploading. The storage account and the `raw-docs` container exist,
but the data plane needs **Storage Blob Data Contributor** on the user
principal, and subscription Owner does not grant it — Owner is management
plane. `just corpus-upload` dry-runs; `--apply` needs that role first.

## 2026-09-15 — Local container stack

`compose.yaml` brings up pgvector on postgres 16, self-hosted Langfuse, and the
API built from a multi-stage uv Dockerfile. `curl localhost:8000/healthz`
returns 200 with a per-dependency report.

Checking versions before writing anything was the right call three times over:

- **Langfuse self-hosted is not one container.** v4 (4.36.1, released today)
  needs postgres, clickhouse, redis and S3-compatible storage — six containers,
  not the single service the task implied. Read the contract out of langfuse's
  own compose rather than reconstructing it; the subtlety that would have cost
  an afternoon is that `LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT` differs between web
  and worker, because the browser resolves the web one.
- **`minio/minio:latest` does not exist.** The official compose uses
  `cgr.dev/chainguard/minio`.
- **`GET /openai/deployments/{name}` is not a data-plane route.** Probing it
  returned 404 for every api-version including a deliberately bogus one, which
  is the tell. `GET /openai/models?api-version=2024-10-21` returns 401 for an
  unauthorised principal and 404 for a bad api-version, so it both works as a
  health probe and proves the version is real.

Langfuse ran its 75 migrations happily against postgres 16, so the two
databases share one server rather than running a second postgres for langfuse.

Two bugs found by running it rather than reading it:

- **psycopg's async mode cannot run on Windows' default ProactorEventLoop.**
  The container is Linux and never sees it, so the API worked in compose and
  failed on the machine it is developed on. `runtime.configure_event_loop_policy`
  fixes it; mypy narrows the `sys.platform` guard correctly on both platforms,
  so CI does not trip over a Windows-only branch.
- **Missing configuration aborted with a traceback**, which in a container is
  indistinguishable from a crash — the thing `load_settings` exists to prevent.
  Settings are now resolved in the CLI before uvicorn takes over: one line on
  stderr, exit 2.

Also moved off `httpx` to `httpx2`, which starlette 1.6 now expects; the old
path emitted a deprecation warning on every test run.

## 2026-09-15 — Azure infrastructure, keyless

Subscription budget first: 40/month, alerting at 50% and 80% actual and 100%
forecasted. It is subscription-scoped on purpose, so tearing the resource group
down does not remove the spend guard. The amount is in the subscription billing
currency — worth confirming that is EUR before trusting it as €40.

Then `infra/main.bicep`: resource group, Log Analytics + Application Insights,
storage with a `raw-docs` container, Azure OpenAI with two deployments, AI
Search on Basic with the semantic ranker, Key Vault, a Container Apps
environment, and a user-assigned identity holding every data-plane role.
Rationale in [ADR 0002](adr/0002-keyless-azure-access.md).

Preflight validation earned its keep twice, before anything was deployed:

- **`gpt-4o-mini` cannot be deployed any more.** `ServiceModelDeprecated`, new
  deployments blocked since 2026-03-31. Confusingly the model catalogue still
  reports an *inference* deprecation date of 2027-04-14 — that is when existing
  deployments stop serving, not when you can still create one. Switched to
  `gpt-4.1-mini`.
- **The two models need different SKUs.** On a fresh subscription in Sweden
  Central, `gpt-4.1-mini` has GlobalStandard quota 200 and no Standard quota;
  `text-embedding-3-large` is the exact mirror image — Standard 350,
  GlobalStandard 0. A single `deploymentSku` parameter could not have worked.
  The quota bucket is also spelled `gpt4.1-mini`, not `gpt-4.1-mini`, which is
  why the first quota query came back empty and looked like "no quota".

West Europe was the obvious first choice and is wrong: it offers no plain
`Standard` SKU for the mini models at all.

Then deployed `dev` for real, which found what validation could not.

**The under-ten-minutes criterion failed: 16m 52s cold.** Azure AI Search
accounts for 16m 34s of it; every other module finished within 2m 08s, and
Search is already provisioned in parallel with the rest. A second deploy took
**80s** and changed nothing — storage `creationTime` unmoved, still exactly four
role assignments rather than eight — so idempotency holds.

Two bugs that only a real run could surface:

- **`print_app_env` never worked.** The outputs were formatted by a Python
  one-liner inside `python -c '...'`, and `outputs[key]['value']` closed the
  shell's single quote. The deployment succeeded and the script still exited 1.
  Rewritten without an embedded parser. Second lesson in the same area: `az -o
  tsv` prints one array element per line rather than tab-separating them, and
  adds CR on Windows.
- **Application Insights auto-creates a Failure Anomalies alert rule** in a
  nested deployment that fails with `MissingSubscriptionRegistration` unless
  `Microsoft.AlertsManagement` is registered. It does not fail the parent
  deployment, so it is easy to miss. Added to the provider list.

Open: `dev` is deployed and costing roughly €2.50/day, almost all of it the
Search service. Teardown is documented but not run — `az group delete -n
rg-fleet-copilot-dev --yes`, plus purging the soft-deleted Key Vault and OpenAI
account if redeploying inside the 7-day retention window.

## 2026-09-15 — Repository skeleton and Python toolchain

Stood the repository up: `uv init --package` with a `src/` layout, the five
pipeline packages (`ingest`, `retrieval`, `agents`, `evals`, `api`), ruff with
`I`/`B`/`UP`/`ASYNC`, `mypy --strict`, pytest with pytest-asyncio, pre-commit,
a justfile, and CI that calls the same recipes. Rationale in
[ADR 0001](adr/0001-python-toolchain-and-quality-gates.md).

The first real module is `ingest.models`: frozen pydantic v2 `Document` and
`Chunk`, with validators that reject naive timestamps and spans that disagree
with their own text. `ingest.loader` is the async surface.

Three things worth remembering:

- **The `ASYNC` rules paid for themselves before the first push.** The first
  draft of `loader.py` called `Path.resolve()` and `Path.glob()` straight from
  a coroutine. `ASYNC240` caught both; the blocking calls now go through
  `asyncio.to_thread`. Nobody would have noticed until the API stalled under
  concurrent ingest.
- **The pydantic mypy plugin catches frozen-model mutation statically.** The
  test that asserts `Document` is frozen needed an explicit
  `# type: ignore[misc]` to compile — the static check fires before the runtime
  one can. Kept the test anyway: the ignore documents that both guards exist.
- **`@computed_field` over `@property` still needs
  `# type: ignore[prop-decorator]`** under mypy. `warn_unused_ignores` is on, so
  these disappear on their own when mypy grows support.

Verified the acceptance criteria: `just lint typecheck test` is green, and
staging a file with an unused import gets the commit rejected by the ruff hook
(`Found 1 error (1 fixed, 0 remaining)` → `files were modified by this hook`),
with `HEAD` unmoved.

**Follow-up, same day: CI failed on the first run.** `astral-sh/setup-uv@v9`
does not resolve. The action publishes floating major aliases only up to `v7`;
`v9.0.0` and `v10.1.0` exist as exact release tags, but `v8`/`v9`/`v10` do not
exist as refs. Pinned to `v10.1.0` with a comment, because the obvious
"tidy-up" here is to shorten it to `@v10` and break the pipeline again.

The same pass removed a second assumption that had not failed yet: the job
added `$HOME/.local/bin` to `GITHUB_PATH` so `just` would be callable. That is
uv's default tool bin directory, not a guarantee. The job now sets setup-uv's
`tool-bin-dir` explicitly and puts *that* on the PATH.

Lesson: a version in a documentation snippet is not evidence the ref exists.
Both of these were checkable against the GitHub API in seconds, and neither was
checked before pushing.

Open: `retrieval`, `agents` and `evals` are empty. `just eval` runs a harness
that discovers zero suites — wired up so the first eval has somewhere to land
rather than arriving with its own bespoke runner.
