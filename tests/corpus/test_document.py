"""The document intermediate holds the front-matter contract and block shapes."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from fleet_copilot.corpus.document import DocumentPlan, FrontMatter, Section, Table
from fleet_copilot.corpus.models import DocumentType, Language


def front_matter_data(**overrides: Any) -> dict[str, Any]:
    """A valid :class:`FrontMatter` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "doc_id": "sd50b-maint-battery-care-rev4",
        "type": "maintenance_procedure",
        "machine_types": ["SD-50B"],
        "item_numbers": ["1.512-340.0"],
        "serials": [],
        "site": None,
        "language": "en",
        "revision": 4,
        "effective_date": "2026-04-12",
    }
    return data | overrides


def plan_data(**overrides: Any) -> dict[str, Any]:
    """A valid :class:`DocumentPlan` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "front_matter": front_matter_data(),
        "title": "Battery care",
        "sections": [
            {
                "heading": "Charging",
                "blocks": [
                    {"kind": "paragraph", "text": "Charge in a ventilated area."},
                    {"kind": "steps", "items": ["Park the machine.", "Connect the charger."]},
                    {"kind": "bullets", "items": ["Never charge above 40 C."]},
                    {
                        "kind": "table",
                        "header": ["Interval", "Action"],
                        "rows": [["Weekly", "Check cell voltages"]],
                    },
                ],
            }
        ],
    }
    return data | overrides


class TestTable:
    def test_rejects_a_row_narrower_than_the_header(self) -> None:
        payload = {"header": ["Interval", "Action"], "rows": [["Weekly"]]}
        with pytest.raises(ValidationError, match="row 0 has 1 cells but the header has 2"):
            Table.model_validate(payload)

    def test_rejects_a_row_wider_than_the_header(self) -> None:
        payload = {"header": ["Interval"], "rows": [["Weekly", "Check cells"]]}
        with pytest.raises(ValidationError, match="row 0 has 2 cells but the header has 1"):
            Table.model_validate(payload)

    def test_names_the_offending_row(self) -> None:
        payload = {
            "header": ["Interval", "Action"],
            "rows": [["Weekly", "Check cells"], ["Monthly"]],
        }
        with pytest.raises(ValidationError, match="row 1 has"):
            Table.model_validate(payload)

    def test_allows_an_empty_cell(self) -> None:
        """A blank cell is legitimate; a missing column is not."""
        table = Table.model_validate({"header": ["Interval", "Note"], "rows": [["Weekly", ""]]})
        assert table.rows[0][1] == ""


class TestFrontMatter:
    def test_sorts_and_deduplicates_reference_lists(self) -> None:
        payload = front_matter_data(
            machine_types=["SDR-90", "SD-50B", "SD-50B"],
            item_numbers=["1.512-341.0", "1.512-340.0"],
        )
        front = FrontMatter.model_validate(payload)
        assert front.machine_types == ("SD-50B", "SDR-90")
        assert front.item_numbers == ("1.512-340.0", "1.512-341.0")

    @pytest.mark.parametrize(
        "bad",
        ["SD50B-Manual", "sd50b manual", "sd50b/manual", "-leading", "trailing-", "sd50b--x", ""],
    )
    def test_rejects_a_doc_id_that_would_not_survive_a_blob_name(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            FrontMatter.model_validate(front_matter_data(doc_id=bad))

    @pytest.mark.parametrize("bad", ["SD50B-26-01042", "sd50b-2026-01042", "SD50B-2026-1042"])
    def test_rejects_a_malformed_serial(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            FrontMatter.model_validate(front_matter_data(serials=[bad]))

    def test_accepts_a_well_formed_serial(self) -> None:
        front = FrontMatter.model_validate(front_matter_data(serials=["SD50B-2026-01042"]))
        assert front.serials == ("SD50B-2026-01042",)

    def test_rejects_revision_zero(self) -> None:
        with pytest.raises(ValidationError):
            FrontMatter.model_validate(front_matter_data(revision=0))

    def test_rejects_an_unknown_key(self) -> None:
        """ADR 0003 fixes the key set; an extra key is a contract change."""
        with pytest.raises(ValidationError):
            FrontMatter.model_validate(front_matter_data(planted=True))

    def test_reference_lists_default_to_empty(self) -> None:
        payload = front_matter_data()
        for key in ("machine_types", "item_numbers", "serials"):
            payload.pop(key)
        front = FrontMatter.model_validate(payload)
        assert (front.machine_types, front.item_numbers, front.serials) == ((), (), ())

    def test_parses_the_effective_date(self) -> None:
        front = FrontMatter.model_validate(front_matter_data())
        assert front.effective_date == date(2026, 4, 12)
        assert front.type is DocumentType.MAINTENANCE_PROCEDURE
        assert front.language is Language.EN


class TestDocumentPlan:
    def test_accepts_every_block_kind(self) -> None:
        plan = DocumentPlan.model_validate(plan_data())
        kinds = [block.kind for block in plan.sections[0].blocks]
        assert kinds == ["paragraph", "steps", "bullets", "table"]

    def test_rejects_a_document_with_no_sections(self) -> None:
        with pytest.raises(ValidationError):
            DocumentPlan.model_validate(plan_data(sections=[]))

    def test_rejects_a_section_with_no_blocks(self) -> None:
        with pytest.raises(ValidationError):
            Section.model_validate({"heading": "Charging", "blocks": []})

    def test_exposes_doc_id_and_language(self) -> None:
        plan = DocumentPlan.model_validate(plan_data())
        assert plan.doc_id == "sd50b-maint-battery-care-rev4"
        assert plan.language is Language.EN

    def test_plain_text_reaches_every_block_kind(self) -> None:
        plan = DocumentPlan.model_validate(plan_data())
        text = plan.plain_text()
        for expected in (
            "Battery care",
            "Charging",
            "Charge in a ventilated area.",
            "Park the machine.",
            "Never charge above 40 C.",
            "Interval",
            "Check cell voltages",
        ):
            assert expected in text
