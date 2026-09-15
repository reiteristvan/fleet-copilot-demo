# Fleet Copilot task runner.
#
# These are the same commands CI runs -- if it is not a recipe here, CI
# should not be doing it. Run `just` or `just --list` to see them all.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Show the available recipes.
default:
    @just --list

# Create or refresh the dev environment from the lockfile.
sync:
    uv sync --locked

# Install the git hooks into .git/hooks.
hooks: sync
    uv run pre-commit install

# Lint and verify formatting, without changing anything.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Apply lint autofixes and format in place.
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Type-check src and tests under mypy --strict.
typecheck:
    uv run mypy

# Run the test suite. Extra args go to pytest: `just test -k chunk`.
test *args:
    uv run pytest {{args}}

# Run the CLI. Extra args go to the CLI: `just run --version`.
run *args:
    uv run fleet-copilot {{args}}

# Run the offline evaluation suite.
eval:
    uv run python -m fleet_copilot.evals.runner

# Everything the pull-request gate runs, in the same order.
check: lint typecheck test
