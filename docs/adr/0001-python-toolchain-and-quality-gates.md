# 1. Python toolchain and quality gates

Date: 2026-09-15

## Status

Accepted

## Context

This repository will grow an AI pipeline whose failure modes are quiet:
retrieval returns plausible-but-wrong passages, a chunk offset drifts and a
citation points at the wrong paragraph, an agent's output degrades without
anything raising. Those bugs are not caught by a runtime crash.

The cheap defences against that class of bug — strict types, frozen validated
models, a linter that understands async, tests that are trivial to run — only
work if they are present from the first commit. Retrofitting `mypy --strict`
onto an existing codebase is a multi-week project that never gets prioritised;
starting with it costs nothing.

A second problem is drift between what a developer runs and what CI runs. When
those diverge, "works on my machine" becomes a real answer and the pipeline
becomes something to be worked around.

## Decision

- **uv** for environment and dependency management, with a committed `uv.lock`.
  Dev tools live in a PEP 735 `dependency-groups.dev` group, not in the runtime
  dependency set, so installing the package does not drag a linter with it.
- **ruff** for both linting and formatting, with `I`, `B`, `UP` and `ASYNC` on
  top of the default `E`/`F`. `ASYNC` is the load-bearing one for a pipeline
  that will be IO-bound.
- **mypy in strict mode** over `src` *and* `tests`, with the pydantic plugin so
  model constructors are checked rather than trusted.
- **pydantic v2** for every model that crosses a stage boundary, frozen and
  `extra="forbid"`.
- **pytest** with `--strict-markers`/`--strict-config`, and `pytest-asyncio` in
  strict mode so async tests must opt in.
- **just** as the single entry point. CI calls the same recipes a developer
  calls. If CI needs a step, it becomes a recipe.
- **pre-commit** running the fast subset of the gate, so obvious failures never
  reach CI. CI still runs every hook over every file, because hooks can be
  skipped locally and cannot be skipped in the pipeline.

Python 3.12 is the declared floor.

## Consequences

- Contributors need `uv` and `just` on their PATH. Both install in one command;
  this is a smaller tax than a `make`/`venv`/`pip-tools` stack, and `just`
  works identically on Windows and Linux, which `make` does not.
- `mypy --strict` over `tests` means test helpers must be annotated too. This is
  deliberate: untyped test code is where type errors hide.
- Strict settings will occasionally demand a suppression for a real toolchain
  limitation — `@computed_field` over `@property` needs
  `# type: ignore[prop-decorator]` today. Every suppression carries its code and
  a reason, and `warn_unused_ignores` deletes them when the tools catch up.
- Two pinned-version pairs must be kept in step by hand: the `uv-pre-commit` rev
  against the `UV_VERSION` in CI, and the `ruff-pre-commit` rev against the ruff
  in `uv.lock`. `pre-commit autoupdate` handles the second; the first is a
  comment in both files.
- Choosing a single CI interpreter (3.12) rather than a matrix trades a little
  coverage for a simpler pipeline. Revisit when there is behaviour that could
  differ across versions.
