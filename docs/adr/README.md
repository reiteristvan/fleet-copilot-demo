# Architecture decision records

One file per decision that is expensive to reverse. Numbered, immutable once
merged: a decision that turns out wrong gets a *new* ADR that supersedes the old
one, and the old one stays in place with its status updated. The record of what
we believed at the time is the point.

Not every decision needs one. The test is: would someone joining in six months
waste a day re-deriving this, or re-litigate it without new information? If yes,
write it down.

Format: context, decision, consequences. Keep it to a page.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-python-toolchain-and-quality-gates.md) | Python toolchain and quality gates | Accepted |
| [0002](0002-keyless-azure-access.md) | Keyless access to every Azure data plane | Accepted |
| [0003](0003-synthetic-corpus-contract.md) | A deterministic synthetic corpus, and the contract it publishes | Accepted |
| [0004](0004-telemetry-storage-and-partitioning.md) | Native range partitioning for telemetry, not TimescaleDB | Accepted |
| [0005](0005-document-parsing-and-the-layout-cache.md) | Layout parsing through Document Intelligence, and the cache in front of it | Accepted |
| [0006](0006-the-chunk-contract.md) | The chunk contract, and where a chunk's metadata comes from | Accepted |
| [0007](0007-the-embedding-store.md) | Where embeddings live, and who owns the backoff | Accepted |
| [0008](0008-retiring-a-disclosed-endpoint.md) | Retiring a disclosed endpoint, without renaming the environment | Accepted |
| [0009](0009-revision-precedence.md) | Which document wins, and how we know it was superseded | Accepted |
