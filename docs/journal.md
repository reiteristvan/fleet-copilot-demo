# Engineering journal

Newest first. Short entries: what changed, what surprised us, what is still
open. Decisions that outlive a session graduate to an ADR in `docs/adr/`.

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

Three things nearly shipped broken, all of the same shape — correct locally,
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
