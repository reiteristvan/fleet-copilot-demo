"""Which document wins when two describe the same thing differently."""

from __future__ import annotations

from datetime import date

import pytest

from fleet_copilot.corpus.build import manifest_path
from fleet_copilot.corpus.manifest import ManifestEntry, load_manifest
from fleet_copilot.retrieval.revisions import (
    current_doc_ids,
    family_stem,
    superseded_doc_ids,
)

BATTERY_REV3 = "sd-50b-maint-battery-care-rev3"
BATTERY_REV4 = "sd-50b-maint-battery-care-rev4"
BATTERY_HU = "sd-50b-maint-battery-care-hu"


def an_entry(doc_id: str, revision: int) -> ManifestEntry:
    return ManifestEntry.model_validate(
        {
            "doc_id": doc_id,
            "type": "maintenance_procedure",
            "language": "en",
            "format": "markdown",
            "path": f"markdown/{doc_id}.md",
            "sha256": "0" * 64,
            "bytes": 10,
            "revision": revision,
            "effective_date": date(2026, 1, 1),
        }
    )


def the_corpus() -> tuple[ManifestEntry, ...]:
    return load_manifest(manifest_path(None)).documents


def test_the_revision_suffix_is_what_forms_a_family() -> None:
    assert family_stem(BATTERY_REV3) == "sd-50b-maint-battery-care"
    assert family_stem(BATTERY_REV4) == "sd-50b-maint-battery-care"


def test_a_document_without_the_suffix_is_its_own_family() -> None:
    """The stem is only stripped where the corpus actually claims a revision.

    Inferring a family from metadata instead would put eight independent
    handover notes in one group and supersede seven of them.
    """
    assert family_stem("handover-hangar-seven-2026-09-10-27") == (
        "handover-hangar-seven-2026-09-10-27"
    )
    assert family_stem(BATTERY_HU) == BATTERY_HU


def test_the_planted_conflict_resolves_to_the_later_revision() -> None:
    """The reason the flag exists.

    Both documents give a maximum battery charging temperature and they
    disagree. Retrieval that returns revision 3 hands a technician a withdrawn
    safety figure, which is the failure this planted case was built to catch.
    """
    current = current_doc_ids(the_corpus())

    assert BATTERY_REV4 in current
    assert BATTERY_REV3 not in current


def test_the_translation_of_the_current_revision_is_current() -> None:
    """The Hungarian copy is revision 4 and carries no -rev suffix, so it is a
    family of one. A metadata-based rule would have had to argue about whether
    a translation supersedes its original."""
    assert BATTERY_HU in current_doc_ids(the_corpus())


def test_exactly_one_document_in_the_corpus_is_superseded() -> None:
    """Measured, and pinned. A rule that grouped by type, machine and item
    number instead marks 68 of 120 documents superseded -- among them eight
    handover notes that are separate shift events, none of them a revision of
    any other."""
    superseded = superseded_doc_ids(the_corpus())

    assert superseded == {BATTERY_REV3}


def test_every_handover_note_stays_current() -> None:
    """The group the metadata rule would have destroyed, asserted directly."""
    entries = the_corpus()
    current = current_doc_ids(entries)
    handovers = [e.doc_id for e in entries if e.type.value == "handover_note"]

    assert len(handovers) > 1
    assert all(doc_id in current for doc_id in handovers)


def test_a_higher_revision_supersedes_a_lower_one_in_the_same_family() -> None:
    entries = (an_entry("proc-rev1", 1), an_entry("proc-rev2", 2), an_entry("proc-rev5", 5))

    assert current_doc_ids(entries) == {"proc-rev5"}


def test_equal_revisions_in_one_family_are_both_current() -> None:
    """Fail safe. Two documents at the same revision are not evidence that
    either withdrew the other, and demoting one on a tie-break would hide a
    real document to satisfy a rule."""
    entries = (an_entry("proc-rev2", 2), an_entry("proc-rev2-hu", 2))

    assert current_doc_ids(entries) == {"proc-rev2", "proc-rev2-hu"}


def test_the_suffix_must_end_the_id_to_count() -> None:
    """'-rev' in the middle of a name is part of the name, not a revision."""
    assert family_stem("review-of-rev3-process") == "review-of-rev3-process"


def test_no_documents_is_no_current_documents() -> None:
    assert current_doc_ids(()) == set()


@pytest.mark.parametrize("revision", [1, 7])
def test_a_lone_document_is_current_whatever_its_revision(revision: int) -> None:
    """Thirty documents in this corpus sit above revision 1 with no sibling.
    Being revision 3 does not make a document stale; being revision 3 when a
    revision 4 exists does."""
    assert current_doc_ids((an_entry("solo-procedure", revision),)) == {"solo-procedure"}
