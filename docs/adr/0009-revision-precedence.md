# 9. Which document wins, and how we know it was superseded

Date: 2026-09-23

## Status

Accepted

## Context

The corpus contains a planted conflict (ADR 0003): two maintenance procedures
give different maximum battery charging temperatures for the SD-50B, revisions 3
and 4, both indexed and both live.

```
sd-50b-maint-battery-care-rev3    rev=3   2026-01-09
sd-50b-maint-battery-care-rev4    rev=4   2026-04-12
```

Revision 4 withdrew revision 3's figure. Retrieval that returns revision 3 hands
a technician a number that was deliberately retracted, and it does so
confidently, with a correct-looking citation to a real document. The index needs
an `is_current` flag so the scoring profile can demote what has been replaced.

**The manifest does not say which documents are superseded.** Every entry carries
a `revision` integer and nothing that groups entries into a series — no family
id, no `supersedes` link. A revision number alone means nothing: thirty documents
in this corpus sit above revision 1 with no sibling anywhere, and every one of
them is current. Being revision 3 does not make a document stale. Being revision
3 *when a revision 4 of the same document exists* does.

So supersession has to be inferred, and the inference is the decision.

### The metadata rule, and why it fails

The instinctive grouping is `(type, machine_types, item_numbers)`: same kind of
document, same machine, same part, therefore the same series — highest revision
wins.

Measured against this corpus, that rule forms 29 groups and marks **68 of 120
documents superseded**. One group holds eight documents:

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

Eight shift handover notes, written on different days at different sites. They
are independent events. None revises any other; all are revision 1. The rule
sees one group, finds an eight-way tie, breaks it somehow, and demotes seven real
documents. Nothing errors, the index builds, and answers simply come back in the
wrong order — the worst available failure mode.

The defect is not the tie-break. It is that document *topic* was used as evidence
of document *lineage*. Two documents about the same part are not thereby versions
of each other.

## Decision

**A document is superseded only when another document whose id differs solely by
a `-rev<N>` suffix carries a strictly higher revision.**

```python
REVISION_SUFFIX = re.compile(r"-rev\d+$")   # anchored: '-rev' mid-name is a name
family_stem("sd-50b-maint-battery-care-rev3")  ->  "sd-50b-maint-battery-care"
family_stem("handover-hangar-seven-2026-09-10-27")  ->  unchanged
```

Documents sharing a stem are one family; the rest are families of one and are
always current. On this corpus that demotes exactly one document —
`sd-50b-maint-battery-care-rev3` — which is the one the planted case is about.

**Strictly higher, so a tie leaves both current.** Two documents at the same
revision are not evidence that either withdrew the other, and breaking the tie
would hide a real document to satisfy the rule. This is what keeps
`sd-50b-maint-battery-care-hu` — the Hungarian revision 4, carrying no suffix —
current, without anyone having to decide whether a translation supersedes its
original.

**It lives in `retrieval/`, not `corpus/`.** The corpus deliberately makes no
claim about supersession; that absence is the whole problem. Asserting one from
`corpus/` would dress a ranking policy up as a fact about the documents. If a
later stage needs it, it moves.

**It is not a field on `Chunk`.** The flag is resolved at index time from the
manifest. Putting it in the chunk contract would change ADR 0006's ten fields,
and every chunk is already embedded, for something the indexer can look up.

## Consequences

**The planted conflict resolves correctly, and a test says so** against the real
manifest rather than a fixture: revision 4 current, revision 3 not, exactly one
superseded document in 120.

**The rule trusts a naming convention, and will miss a supersession that was
never named.** A document revised in place — a new `revision: 5` under an
unchanged id, or a `-v5` suffix — reads as a family of one and keeps normal rank.

That is the direction worth failing in. This rule fails *safe*: worst case a
stale document is not demoted, and nothing correct is hidden. The metadata rule
fails *unsafe*: it demotes 68 documents that were never superseded, burying
correct answers. A missed supersession is also detectable later, because it
surfaces as two retrieved documents that disagree — which is exactly what the
planted case tests for.

**Changing this means reindexing.** The flag is written into every indexed
document, so the rule is cheap now and expensive after the index is populated.
That is why it is recorded before the index is built rather than alongside it.

**A real corpus would not rely on this.** An operational document system has
explicit supersession — a `supersedes` field, or a lifecycle state. If one
arrives, this rule should be replaced by reading it, not extended. The naming
convention is evidence the synthetic corpus happens to provide, not a design to
carry forward.
