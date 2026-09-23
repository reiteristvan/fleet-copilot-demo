# Open questions

Things that came up while building, were handled well enough to keep moving, and
deserve a decision rather than a default. Not bugs with owners — each one has a
working resolution in the tree already. What is open is whether that resolution
is the one this project wants.

Settled decisions graduate to `docs/adr/`. Things that are just history go in
`docs/journal.md`. This file is the middle: answered for now, not agreed.

---

## 1. Azure data-plane roles are granted one at a time, and each gap costs a run

**What happened.** Embedding from a laptop returned `401 PermissionDenied`. The
developer principal already held *Cognitive Services User*, which is what
Document Intelligence gates on. Azure OpenAI gates on a different role entirely —
*Cognitive Services OpenAI User* — and it had been assigned to the managed
identity only.

This is the third time the same shape has bitten: story 1.2 hit a 403 because
Subscription Owner is a management-plane role carrying no data actions, and its
own docstring had predicted it. Neither error message names a role. The first
said the principal lacks a data action; the second just said "no access". Both
read like a broken token rather than an assignment nobody made.

**Resolved by** adding `developerOpenAi` to `rbac.bicep` and redeploying.

**Resolved, 2026-09-23.** `just preflight` now probes every data plane with a
real read-only call and names the role a failure needs. The two Search roles the
retrieval story needs were added to `rbac.bicep` at the same time.

Deliberately a live call rather than a role listing: when the OpenAI assignment
was finally made, the call kept failing for minutes while it propagated, and a
listing would have reported success throughout.

Writing it immediately caught a fifth gap nobody had named. Listing Search index
definitions passes under Subscription Owner, because Owner carries
`Microsoft.Search/*` — but document read and write are *data* actions and are
not covered. A single Search check would have reported the plane reachable right
up until the first `upload_documents` 403, so Search is checked twice, once per
grant.

---

## 2. psycopg's async driver does not run on Windows

**What happened.** `EmbeddingStore` was specified to use
`psycopg.AsyncConnection`. On Windows it raises
`InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async
mode`, because that is the default event loop there. The fix is either a global
event-loop policy change or the sync driver in a thread.

**Resolved by** using the synchronous driver through `asyncio.to_thread`, which
is what `ingest/cache.py` already does for blob and disk I/O and what CLAUDE.md
blesses. The usual argument for the async driver — not holding a pool slot across
a round trip — does not apply, because the store opens a connection per call and
there is no pool.

**The part that is actually worrying.** `src/fleet_copilot/api/health.py` still
uses `AsyncConnection`, and its `except Exception` is deliberately broad so that
"a health endpoint must report a failure, never become one". The consequence is
that **`/healthz` reports the database as failed on any Windows host, even when
the database is perfectly healthy** — the InterfaceError is caught and rendered
as a dependency error. In the Linux container it works correctly, and the test
only ever asserts the *unreachable* case, so nothing catches this.

**Open.** Pick one story for async Postgres and apply it everywhere: sync +
`to_thread` throughout, or `AsyncConnection` plus a Windows event-loop policy set
once at process start. Also worth a test that distinguishes "database is down"
from "the driver could not run at all" — today they are the same red.

---

## 3. Tests commit rows into the real embedding cache

**What happened.** `EmbeddingStore.put_many` commits, so the store tests leave
rows behind. They were written against the live deployment name, `embeddings`, so
four fake vectors sat in `ingest.embedding_cache` alongside the 1,476 real ones —
enough to make a row count disagree with the reported totals, and in principle
enough to make a cache lookup hit a fake.

**Resolved by** moving the tests to a `test-embeddings` model id, which cannot
collide with a real run, and deleting the four rows.

**Open.** The isolation is by naming convention, which holds only as long as
everyone follows it. The alternatives are a transactional fixture that rolls the
writes back (the store would have to accept an injected connection), or a
dedicated test database. The same question applies to every future test that
writes through a store rather than through the `connection` fixture.

---

## 4. The plan documents specify tests that do not run

**What happened.** Across plans 2 and 3, several specified tests were wrong in
ways that only surface when you execute them:

- `@pytest.mark.usefixtures("database")` — no `database` fixture exists, only
  `database_url`. Would error on every test that used it.
- `zip(chunks, chunks[1:], strict=True)` for a pairwise walk — always raises,
  because the slice is one shorter. `strict=True` had been added to satisfy a
  lint rule rather than to state anything true.
- Fakes typed `object` with four `# type: ignore` comments to make them pass
  mypy, where typing them to match the real Protocol removes every ignore.
- `len(list(records))` after already iterating `records` — returns 0 for a
  generator.
- A test asserting a bare `count(*)` that another test's committed rows change,
  so it passed or failed depending on suite order.

**Resolved by** fixing each as it was hit; the details are in the commit bodies.

**Open.** These plans are detailed enough to look executable, which is what makes
the defects expensive — they are trusted. Either the plan writing step should
stop including literal test bodies and specify behaviour instead, or plans should
be treated explicitly as drafts whose code is a sketch. Worth deciding before the
next plan is written, because the current convention implies a precision the
documents do not have.
