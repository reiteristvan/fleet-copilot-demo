# fleet-copilot-demo

Fleet copilot demo repository is showcasing an end-to-end AI pipeline with an
actual scenario: answering questions over fleet documents — manuals, service
records, incident reports — with citations back to the source passage.

> **Status: skeleton.** `ingest` has working models and an async loader. The
> remaining stages are empty packages. What works end to end today is the
> toolchain.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/):

```console
$ uv tool install rust-just
$ just sync     # create the venv from uv.lock
$ just hooks    # install the pre-commit hooks
$ just check    # lint + typecheck + test
```

`just` on its own lists every recipe. The same recipes run in CI, so a green
local run and a green pipeline mean the same thing.

## Layout

```
src/fleet_copilot/
  ingest/      load source documents and split them into retrievable chunks
  retrieval/   index chunks and fetch the ones relevant to a question
  agents/      plan over and answer from retrieved context
  evals/       score the retrieval and agent layers offline
  api/         entry points: a CLI today, an HTTP service later
tests/         mirrors the source layout
infra/         infrastructure-as-code (nothing provisioned yet)
docs/adr/      architecture decision records
docs/journal.md  engineering journal, newest first
```

## Toolchain

Python 3.12+, [uv](https://docs.astral.sh/uv/) for packaging,
[ruff](https://docs.astral.sh/ruff/) for lint and format,
[mypy](https://mypy-lang.org/) in strict mode, [pytest](https://docs.pytest.org/)
with `pytest-asyncio`, and [pre-commit](https://pre-commit.com/) hooks.

Why this particular set, and which trade-offs it accepts:
[ADR 0001](docs/adr/0001-python-toolchain-and-quality-gates.md).
Conventions for contributors and agents: [CLAUDE.md](CLAUDE.md).
