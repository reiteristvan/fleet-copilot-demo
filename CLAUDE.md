# CLAUDE.md — how agents work in this repository

## What this project is

A fleet copilot: a retrieval-augmented pipeline over fleet documents — manuals,
service records, incident reports. Documents are loaded and chunked (`ingest/`),
indexed and searched (`retrieval/`), answered over (`agents/`), scored offline
(`evals/`), and exposed to callers (`api/`). The system answers questions and
cites its sources — it never dispatches vehicles, schedules work, or writes back
to any operational system.

Right now this is a skeleton. `ingest` has real models and a real loader; the
other stages are empty packages with docstrings stating what belongs in them.
What currently works end to end is the toolchain, not the pipeline.

## Commands

Everything runs through `just` (`uv tool install rust-just` if you do not have it).
These are the same recipes CI runs.

| Command | What it does |
| --- | --- |
| `just sync` | Create/refresh the venv from `uv.lock` |
| `just hooks` | Install the pre-commit hooks |
| `just lint` | `ruff check` + `ruff format --check` |
| `just typecheck` | `mypy --strict` over `src` and `tests` |
| `just test` | `pytest` (extra args pass through: `just test -k chunk`) |
| `just run` | The `fleet-copilot` CLI |
| `just eval` | The offline eval harness |
| `just check` | `lint` + `typecheck` + `test` — the full gate |

## Ground rules (non-negotiable)

1. **`just check` is the gate.** A change is done when it passes, not when the
   code looks right. Paste the output; do not assert it passed.
2. **Never weaken a gate to make a change land.** Adding a rule to ruff's ignore
   list, relaxing a mypy setting, or marking a test `xfail` to get green is a
   revert, not a fix. If a gate is genuinely wrong, say so and stop — that is a
   decision with an ADR attached, not a diff.
3. **Every `noqa` and `type: ignore` carries a code and a reason.** A bare
   `# type: ignore` will not pass review. If the suppression is a known
   toolchain limitation, name it in the comment.
4. **Never touch these without an explicit human go-ahead in the session:**
   - the `[tool.*]` sections of `pyproject.toml`
   - `.pre-commit-config.yaml` and `.github/workflows/`
   - `uv.lock` by hand (change `pyproject.toml` and let `uv` relock)
   - this file
5. **Decisions that are expensive to reverse get an ADR** in `docs/adr/` before
   the code lands, not after.

## Conventions

- **Python 3.12 is the floor.** Use what 3.12 gives you: `X | None`, built-in
  generics, `Self`, `datetime.UTC`.
- **Types are not optional.** `mypy --strict` covers `src` and `tests`. Every
  function, including test functions, is annotated.
- **Domain models are pydantic v2, frozen, and `extra="forbid"`.** A model that
  reaches another stage is known to be well-formed and cannot drift underneath
  it. Validators encode rules that are cheap to enforce at construction time and
  expensive to debug three stages later — and each one's docstring says which
  failure it prevents, not what the code does.
- **Nothing blocking runs in a coroutine.** `pathlib`, `requests`, `time.sleep`
  and friends go through `asyncio.to_thread` or an async client. The `ASYNC`
  rules enforce this; they have already caught one real bug here.
- **Async tests opt in.** `pytest-asyncio` is in strict mode, so async tests
  carry `@pytest.mark.asyncio`.
- **Tests mirror the source layout** (`tests/ingest/…`, `tests/retrieval/…`).
  New behaviour ships with its tests in the same commit.
- **Test invalid input through `model_validate`,** not by lying to the type
  checker with a cast or an ignore on the constructor.
- **A test that commits isolates itself by key, not by cleanup.** Most database
  tests roll back through the `connection` fixture. A store that owns its own
  commit cannot be rolled back by its caller — the embedding cache writes per
  batch on purpose, so an interrupted run keeps what it paid for — so its tests
  share a table with real data. Give them a namespace that cannot collide
  (`model_id="test-embeddings"`) rather than a teardown that deletes: a delete
  runs only if the test got that far, while a key that can never match a real
  run is safe even when the test dies halfway. Isolation by naming is a
  convention and is only as good as review; it is chosen here because the cost
  of it failing is a few junk rows in a developer's cache, and the alternatives
  either invert a deliberate design decision about who owns the commit or stop
  the tests touching the database the application actually uses.
- **Comment only what the code cannot say itself.** Names and structure carry
  the *what*; a comment earns its place when it records *why* — a constraint, a
  rejected alternative, a non-obvious failure it prevents, or an external fact
  that would otherwise have to be rediscovered (an API version that does not
  resolve, a quota, a pinned tag that must not be shortened). A comment that
  restates the line below it is noise and will be removed in review.
- **Never introduce key-based auth to an Azure data plane.** The Bicep disables
  it at the resource level, so key-based code fails at runtime rather than in
  review. Use `get_credential()` from `fleet_copilot.credentials`.
- **Pin Azure API versions and model versions to values you have checked.**
  `az provider show`, `az cognitiveservices model list` and
  `./infra/deploy.sh <env> --validate` are cheap; a version that resolves in a
  documentation snippet is not evidence it resolves here.
- **Commits are atomic and semantic** (`feat(ingest): …`, `build: …`, `ci: …`).
  One concern per commit. The body says *why*, since the diff already says what.

## How to verify your own work before handing it back

1. `just check` — the mechanical floor. All three stages, not just the one you
   think you touched.
2. `uv run pre-commit run --all-files` — catches what the hooks would catch on
   someone else's machine.
3. Re-read the task you were given and confirm you did not wander outside it.
   Scope creep is the most common failure mode here: an empty package is empty
   on purpose.

## When you are unsure

Say so and stop. An explicit "this is ambiguous, options are A/B" costs one
round trip; a confident wrong guess costs a revert and trust. This repo
optimizes for the former.
