# Engineering journal

Newest first. Short entries: what changed, what surprised us, what is still
open. Decisions that outlive a session graduate to an ADR in `docs/adr/`.

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

Everything is validated but **not deployed** — `az deployment sub validate`
passes for both `dev` and `ci`, and what-if reports 14 resources to create.

Open: teardown is documented but not run. `az group delete -n
rg-fleet-copilot-dev --yes`, plus purging the soft-deleted Key Vault and OpenAI
account if redeploying within the 7-day retention window.

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
