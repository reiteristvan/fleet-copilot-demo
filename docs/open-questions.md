# Open questions

Things that came up while building and were handled well enough to keep moving.
Each has a working resolution in the tree. What is open is whether that
resolution is the one this project wants.

Settled decisions graduate to `docs/adr/`. History goes to `docs/journal.md`.
This file holds the middle: answered for now, not agreed.

An entry leaves this file when the decision is made. It does not stay as a
record. The record is the code, the ADR, or the journal entry.

## Open

None.

## Closed

| # | Question | Decided | Where the decision lives |
| --- | --- | --- | --- |
| 1 | Azure data-plane roles are granted one at a time | 2026-09-23 | `src/fleet_copilot/preflight.py`, `infra/modules/rbac.bicep` |
| 2 | psycopg's async driver does not run on Windows | 2026-09-23 | `src/fleet_copilot/api/health.py` |
| 3 | Tests commit rows into the real embedding cache | 2026-09-23 | `CLAUDE.md`, Conventions |
| 4 | Plans specify tests that do not run | 2026-09-24 | `docs/superpowers/plans/README.md` |

The reasoning for each, and the alternatives rejected, is in the journal entry
for 2026-09-24.
