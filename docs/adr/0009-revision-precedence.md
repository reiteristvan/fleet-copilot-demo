# 9. Which document wins, and how we know it was superseded

Date: 2026-09-23

## Status

Accepted

## Context

The corpus contains a planted conflict (ADR 0003). Two maintenance procedures
give different maximum battery charging temperatures for the SD-50B. Both are
indexed and both are live.

```
sd-50b-maint-battery-care-rev3    rev=3   2026-01-09
sd-50b-maint-battery-care-rev4    rev=4   2026-04-12
```

Revision 4 withdrew revision 3's figure. Retrieval that returns revision 3 gives
a technician a retracted number, with a correct-looking citation to a real
document. The index needs an `is_current` flag so the scoring profile can demote
what has been replaced.

**The manifest does not say which documents are superseded.** Every entry has a
`revision` integer. Nothing groups entries into a series. There is no family id
and no `supersedes` link.

A revision number alone means nothing. Thirty documents in this corpus sit above
revision 1 with no sibling, and all of them are current. Revision 3 does not make
a document stale. Revision 3 when a revision 4 of the same document exists does.

So supersession has to be inferred. The inference is the decision.

### The metadata rule, and why it fails

The obvious grouping is `(type, machine_types, item_numbers)`. Same kind of
document, same machine, same part, therefore the same series. Highest revision
wins.

That rule was measured against this corpus. It forms 29 groups and marks **68 of
120 documents superseded**. One group holds eight documents:

```
handover-city-hospital-2026-01-09-31        rev=1   2026-01-09
handover-riverside-mall-2026-03-09-34       rev=1   2026-03-09
handover-city-hospital-2026-04-04-21        rev=1   2026-04-04
handover-debrecen-uzem-2026-06-30-02-hu     rev=1   2026-06-30
handover-hangar-seven-2026-08-01-28         rev=1   2026-08-01
handover-hangar-seven-2026-08-24-20         rev=1   2026-08-24
handover-riverside-mall-2026-09-02-02       rev=1   2026-09-02
handover-hangar-seven-2026-09-10-27         rev=1   2026-09-10
```

These are eight shift handover notes from different days and different sites.
They are independent events. None revises any other, and all are revision 1. The
rule sees one group, finds an eight-way tie, breaks it somehow, and demotes seven
real documents.

Nothing errors. The index builds. Answers come back in the wrong order. That is
the worst available failure mode.

The defect is not the tie-break. The rule used document topic as evidence of
document lineage. Two documents about the same part are not versions of each
other.

## Decision

**A document is superseded only when another document whose id differs solely by
a `-rev<N>` suffix carries a strictly higher revision.**

```python
REVISION_SUFFIX = re.compile(r"-rev\d+$")   # anchored: '-rev' mid-name is a name
family_stem("sd-50b-maint-battery-care-rev3")       ->  "sd-50b-maint-battery-care"
family_stem("handover-hangar-seven-2026-09-10-27")  ->  unchanged
```

Documents sharing a stem are one family. The rest are families of one and are
always current. On this corpus that demotes exactly one document,
`sd-50b-maint-battery-care-rev3`, which is the one the planted case is about.

**Strictly higher, so a tie leaves both current.** Two documents at the same
revision are not evidence that either withdrew the other. Breaking the tie would
hide a real document to satisfy the rule. This keeps
`sd-50b-maint-battery-care-hu` current. That is the Hungarian revision 4, and it
carries no suffix. Nobody has to decide whether a translation supersedes its
original.

**It lives in `retrieval/`, not `corpus/`.** The corpus makes no claim about
supersession. That absence is the problem. Asserting one from `corpus/` would
present a ranking policy as a fact about the documents. If a later stage needs
it, move it.

**It is not a field on `Chunk`.** The indexer resolves the flag from the manifest
at index time. Putting it in the chunk contract would change ADR 0006's ten
fields, and every chunk is already embedded, for a value the indexer can look up.

## Consequences

**The planted conflict resolves correctly, and a test says so.** The test runs
against the real manifest rather than a fixture. Revision 4 is current, revision
3 is not, and exactly one document in 120 is superseded.

**The rule trusts a naming convention and will miss an unnamed supersession.** A
document revised in place, with a new `revision: 5` under an unchanged id or a
`-v5` suffix, reads as a family of one and keeps normal rank.

That is the direction worth failing in. This rule fails safe: at worst a stale
document is not demoted, and nothing correct is hidden. The metadata rule fails
unsafe: it demotes 68 documents that were never superseded and buries correct
answers. A missed supersession is also detectable later, because it shows up as
two retrieved documents that disagree. That is what the planted case tests for.

**Changing this means reindexing.** The flag is written into every indexed
document. The rule is cheap now and expensive once the index is populated. That
is why it is recorded before the index is built.

**A real corpus would not rely on this.** An operational document system has
explicit supersession, through a `supersedes` field or a lifecycle state. If one
arrives, replace this rule by reading it. Do not extend the rule. The naming
convention is evidence the synthetic corpus happens to provide, not a design to
carry forward.
