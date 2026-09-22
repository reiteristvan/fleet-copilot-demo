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

# Regenerate the synthetic document corpus into data/. Output is deterministic.
corpus:
    uv run python scripts/gen_corpus.py

# Report drift between the committed corpus and the seed data, writing nothing.
corpus-check:
    uv run python scripts/gen_corpus.py --check

# Dry-run the corpus upload to Blob Storage: `just corpus-upload --apply` writes.
corpus-upload *args:
    uv run python scripts/upload_corpus.py {{args}}

# Dry-run the corpus parse: `just corpus-parse --apply` calls the service.
corpus-parse *args:
    uv run python scripts/parse_corpus.py {{args}}

# Chunk-size distribution per strategy: `just chunk-stats --all` includes PDFs.
chunk-stats *args:
    uv run python scripts/chunk_stats.py {{args}}

# Dry-run the embedding pass: `just embed-corpus --apply` calls the model.
embed-corpus *args:
    uv run python scripts/embed_corpus.py {{args}}

# Apply every database migration. Needs the stack up (`just up`).
db-migrate:
    uv run alembic upgrade head

# Roll the schema all the way back, then forward. Proves it applies from scratch.
db-reset:
    uv run alembic downgrade base
    uv run alembic upgrade head

# Generate the fleet and load it, replacing whatever is there. Takes ~2 minutes.
db-seed:
    uv run python scripts/gen_fleet.py

# Show the fleet the seed would build, without touching the database.
fleet-preview:
    uv run python scripts/gen_fleet.py --dry-run

# Run the six reference queries as copilot_ro and report how long each takes.
db-queries:
    uv run python -m fleet_copilot.fleet.report

# Start the local stack (postgres + langfuse + the API) and wait for health.
up:
    docker compose up -d --build --wait

# Stop the local stack, keeping volumes.
down:
    docker compose down

# Stop the local stack and delete its data.
reset:
    docker compose down --volumes

# Tail logs for one service: `just logs api`.
logs service="":
    docker compose logs -f {{service}}

# Everything the pull-request gate runs, in the same order.
check: lint typecheck test
