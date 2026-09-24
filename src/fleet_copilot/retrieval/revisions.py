"""Which document wins when two of them describe the same thing differently.

The corpus carries a planted conflict. Two maintenance procedures give different
maximum battery charging temperatures for the SD-50B, revisions 3 and 4, and
both are live. Retrieval that returns revision 3 hands a technician a withdrawn
safety figure. So the index needs to know which documents have been superseded.

The manifest does not say. It gives every document a ``revision`` integer and
nothing that groups documents into a series. There is no family id and no
`supersedes` link. Revision 3 of *what* is the question, and it has to be
inferred.

**Inferred from the doc_id, not from metadata.** A document is superseded only
when another document whose id differs solely by a ``-rev<N>`` suffix carries a
higher revision. Everything else is a family of one and is current.

Grouping by ``(type, machine_types, item_numbers)`` is the tempting alternative.
It is badly wrong on this corpus. It forms 29 groups and marks 68 of 120
documents superseded. One group holds eight handover notes for the same machine
and part. They are separate shift events, written on different days at different
sites, every one revision 1, none a revision of any other. Demoting seven of
them would bury correct answers behind a reasonable-looking ranking.

This rule trusts a naming convention. It will miss a supersession that was never
named, which leaves a stale document at normal rank rather than hiding a live
one. That is the direction worth failing in (ADR 0009).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from fleet_copilot.corpus.manifest import ManifestEntry

REVISION_SUFFIX = re.compile(r"-rev\d+$")
"""Anchored to the end: '-rev' inside a name is part of the name."""


def family_stem(doc_id: str) -> str:
    """The id with its revision suffix removed.

    Documents sharing a stem are revisions of one another. A document with no
    suffix is its own stem, so it is its own family.
    """
    return REVISION_SUFFIX.sub("", doc_id)


def _highest_revision_by_family(entries: Iterable[ManifestEntry]) -> dict[str, int]:
    highest: dict[str, int] = defaultdict(int)
    for entry in entries:
        stem = family_stem(entry.doc_id)
        highest[stem] = max(highest[stem], entry.revision)
    return dict(highest)


def current_doc_ids(entries: Iterable[ManifestEntry]) -> set[str]:
    """The documents nothing else in their family has replaced.

    Strictly higher, so two documents at the same revision are both current. A
    tie is not evidence that either withdrew the other. Breaking it would hide a
    real document to satisfy the rule.
    """
    entries = tuple(entries)
    highest = _highest_revision_by_family(entries)
    return {
        entry.doc_id for entry in entries if entry.revision >= highest[family_stem(entry.doc_id)]
    }


def superseded_doc_ids(entries: Iterable[ManifestEntry]) -> set[str]:
    """The complement of :func:`current_doc_ids`."""
    entries = tuple(entries)
    current = current_doc_ids(entries)
    return {entry.doc_id for entry in entries if entry.doc_id not in current}
