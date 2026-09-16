# 4. Native range partitioning for telemetry, not TimescaleDB

Date: 2026-09-16

## Status

Accepted

## Context

The fleet emits one telemetry sample per powered-on minute per machine. Forty
machines over ninety days is roughly 2.6 million rows, and that is the *demo*
scale: a real contract cleaner running a thousand machines would produce the
same volume every four days.

Every query the agent asks of this data is time-bounded and usually
machine-bounded — utilisation over the last seven days, temperature excursions
by day, distance travelled per machine. None of them wants the whole table, and
none of them wants a sample: they want an aggregate the rollups already hold.

Three ways to store it were considered.

**One unpartitioned table with a BRIN index on `ts`.** Simplest, and BRIN is
genuinely good on naturally time-ordered data. But there is no cheap way to drop
old data — `DELETE` on millions of rows leaves bloat and a long vacuum — and a
correlated BRIN degrades as soon as backfill or out-of-order arrival breaks the
physical ordering, which a real ingest path does routinely.

**TimescaleDB hypertables.** The best fit on paper: automatic chunking,
continuous aggregates that refresh incrementally rather than fully, and
compression. The costs are not technical merit but deployment reality. The
container image this project uses is `pgvector/pgvector:pg16`, which carries no
TimescaleDB, so adopting it means building and maintaining a custom image
carrying both extensions, or running a second database. On the deployed side,
Azure Database for PostgreSQL Flexible Server offers TimescaleDB only as the
Apache-licensed edition, which excludes exactly the two features that would have
justified it — compression and continuous aggregates.

**Native declarative range partitioning by month.** In core PostgreSQL since 10,
with partition pruning, per-partition indexes, and `DETACH`/`DROP` for
retention.

## Decision

`fleet.telemetry_sample` is `PARTITION BY RANGE (ts)` with one partition per
calendar month, created explicitly by migration for the months the corpus
timeline covers.

The rollups — `fleet.telemetry_hourly` and `fleet.telemetry_daily` — are plain
materialised views, refreshed by the seed. They are what the reporting views and
therefore the agent read; raw samples are never exposed.

Consequences accepted deliberately:

- **The primary key must contain the partition key**, so it is
  `(serial, ts)` rather than `(serial)`. This is not a compromise: a telemetry
  sample is identified by machine and instant.
- **Partitions do not appear by themselves.** A row outside every declared range
  is rejected rather than silently stored, which is the right default — a
  timestamp in 2038 is a bug, not a row. Extending the range is a migration, and
  the seed asserts the window it is about to write is covered.
- **Materialised views refresh in full.** At this volume a full refresh is
  seconds. The incremental refresh a continuous aggregate would give is the one
  thing genuinely lost by not using TimescaleDB, and it is worth revisiting if
  the sample table grows past roughly a hundred million rows.

## Consequences

- No extension beyond `btree_gist`, which the state-interval exclusion
  constraint needs anyway. The stock `pgvector/pgvector:pg16` image runs the
  whole schema, and so does Azure Flexible Server without a feature tier.
- Retention becomes `DROP TABLE` on one partition — constant time, no bloat, no
  vacuum — instead of a long `DELETE`.
- Queries that filter on `ts` prune to the months they touch. Queries that do
  not filter on `ts` scan everything, which is a good reason for the agent to
  read the rollups rather than the samples, and it does not have the choice: the
  reporting schema exposes no path to the partitioned table.
- Revisit if sample volume grows two orders of magnitude, or if incremental
  aggregate refresh becomes the bottleneck. Superseding this ADR would mean a
  custom image and a licence check, not just a schema change, which is why the
  reasoning above is recorded rather than the conclusion alone.
