"""The seed-data models reject the bad catalogues that are expensive to debug.

Every invalid case goes through ``model_validate`` on a plain dict rather than
through the constructor with a cast: the point is that bad YAML is rejected when
it is loaded, and a test that has to lie to the type checker is not testing that.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from fleet_copilot.corpus.models import (
    Catalogue,
    CorpusSpec,
    DocumentType,
    ErrorCode,
    Language,
    MachineType,
    TypeQuota,
    Variant,
)


def variant_data(item_number: str = "1.512-340.0", **overrides: Any) -> dict[str, Any]:
    """A valid :class:`Variant` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "item_number": item_number,
        "battery": "agm",
        "deck": "brush",
        "solution_tank_l": 50,
        "recovery_tank_l": 55,
    }
    return data | overrides


def machine_data(code: str = "SD-50B", **overrides: Any) -> dict[str, Any]:
    """A valid :class:`MachineType` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "code": code,
        "family": "scrubber_dryer_walk_behind",
        "name_en": "Walk-behind scrubber-dryer SD-50B",
        "name_hu": "Kezi vezetesu surolo-szaritogep SD-50B",
        "variants": [variant_data(), variant_data("1.512-341.0", battery="lithium-ion")],
    }
    return data | overrides


def catalogue_data(**overrides: Any) -> dict[str, Any]:
    """A valid :class:`Catalogue` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "machine_types": [machine_data()],
        "error_codes": [
            {
                "code": "E-041",
                "title_en": "Battery over-temperature",
                "title_hu": "Akkumulator tulmelegedes",
                "applies_to": ["SD-50B"],
            }
        ],
        "sites": [{"slug": "depot-north", "name": "Depot North", "language": "en"}],
    }
    return data | overrides


def spec_data(**overrides: Any) -> dict[str, Any]:
    """A valid :class:`CorpusSpec` payload, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "seed": 20260916,
        "total": 10,
        "quotas": [
            {"type": "operator_manual", "en": 4, "hu": 2},
            {"type": "glossary", "en": 3, "hu": 1},
        ],
        "pdf_count": 2,
        "scanned_pdf_count": 1,
        "docx_count": 1,
    }
    return data | overrides


class TestVariant:
    def test_accepts_a_well_formed_variant(self) -> None:
        variant = Variant.model_validate(variant_data())
        assert variant.item_number == "1.512-340.0"

    def test_rejects_a_recovery_tank_smaller_than_the_solution_tank(self) -> None:
        payload = variant_data(solution_tank_l=60, recovery_tank_l=40)
        with pytest.raises(ValidationError, match="smaller than the"):
            Variant.model_validate(payload)

    def test_accepts_equal_tanks(self) -> None:
        """The boundary is legal: some machines really do ship matched tanks."""
        variant = Variant.model_validate(variant_data(solution_tank_l=50, recovery_tank_l=50))
        assert variant.recovery_tank_l == 50

    @pytest.mark.parametrize(
        "bad",
        ["1512-340.0", "1.51-340.0", "1.512-3400.0", "1.512-340", "", "1.512-340.00"],
    )
    def test_rejects_an_item_number_that_is_not_in_the_manufacturer_format(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            Variant.model_validate(variant_data(item_number=bad))

    def test_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            Variant.model_validate(variant_data(colour="yellow"))


class TestMachineType:
    def test_rejects_a_scrubber_dryer_variant_with_no_tanks(self) -> None:
        bare = variant_data("1.512-350.0")
        del bare["solution_tank_l"]
        del bare["recovery_tank_l"]
        payload = machine_data(variants=[variant_data(), bare])
        with pytest.raises(ValidationError, match="needs both tanks"):
            MachineType.model_validate(payload)

    def test_rejects_a_sweeper_variant_with_no_hopper(self) -> None:
        payload = machine_data(
            code="SWR-120",
            family="sweeper_ride_on",
            variants=[
                variant_data(
                    "1.721-105.0", hopper_l=60, solution_tank_l=None, recovery_tank_l=None
                ),
                variant_data("1.721-106.0", solution_tank_l=None, recovery_tank_l=None),
            ],
        )
        with pytest.raises(ValidationError, match="needs a hopper capacity"):
            MachineType.model_validate(payload)

    def test_accepts_a_sweeper_described_by_its_hopper(self) -> None:
        """A sweeper has no tanks at all, and that is not missing data."""
        payload = machine_data(
            code="SWR-120",
            family="sweeper_ride_on",
            variants=[
                variant_data(
                    "1.721-105.0", hopper_l=60, solution_tank_l=None, recovery_tank_l=None
                ),
                variant_data(
                    "1.721-106.0", hopper_l=80, solution_tank_l=None, recovery_tank_l=None
                ),
            ],
        )
        machine = MachineType.model_validate(payload)
        assert machine.variants[0].hopper_l == 60
        assert machine.variants[0].solution_tank_l is None

    def test_accepts_a_single_disc_machine_with_neither(self) -> None:
        """A single-disc machine is described by its deck, not by a capacity."""
        payload = machine_data(
            code="SDM-43",
            family="single_disc",
            variants=[
                variant_data("1.291-100.0", solution_tank_l=None, recovery_tank_l=None),
                variant_data("1.291-101.0", solution_tank_l=None, recovery_tank_l=None),
            ],
        )
        assert MachineType.model_validate(payload).code == "SDM-43"

    def test_rejects_a_repeated_item_number(self) -> None:
        payload = machine_data(variants=[variant_data(), variant_data()])
        with pytest.raises(ValidationError, match="repeats an item number"):
            MachineType.model_validate(payload)

    def test_rejects_a_single_variant(self) -> None:
        """The catalogue promises two or three configurations per type."""
        with pytest.raises(ValidationError):
            MachineType.model_validate(machine_data(variants=[variant_data()]))

    @pytest.mark.parametrize("bad", ["sd-50b", "SD50B", "S-50B?", ""])
    def test_rejects_a_malformed_machine_code(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            MachineType.model_validate(machine_data(code=bad))

    def test_name_follows_the_language(self) -> None:
        machine = MachineType.model_validate(machine_data())
        assert machine.name(Language.EN).startswith("Walk-behind")
        assert machine.name(Language.HU).startswith("Kezi")


class TestErrorCode:
    @pytest.mark.parametrize("bad", ["E041", "E-41", "e-041", "E-0411"])
    def test_rejects_a_malformed_code(self, bad: str) -> None:
        payload = {
            "code": bad,
            "title_en": "Battery over-temperature",
            "title_hu": "Akkumulator tulmelegedes",
            "applies_to": ["SD-50B"],
        }
        with pytest.raises(ValidationError):
            ErrorCode.model_validate(payload)

    def test_title_follows_the_language(self) -> None:
        error = ErrorCode.model_validate(
            {
                "code": "E-041",
                "title_en": "Battery over-temperature",
                "title_hu": "Akkumulator tulmelegedes",
                "applies_to": ["SD-50B"],
            }
        )
        assert error.title(Language.EN) == "Battery over-temperature"
        assert error.title(Language.HU) == "Akkumulator tulmelegedes"


class TestCatalogue:
    def test_accepts_a_well_formed_catalogue(self) -> None:
        catalogue = Catalogue.model_validate(catalogue_data())
        assert catalogue.machine("SD-50B").code == "SD-50B"
        assert catalogue.errors_for("SD-50B")[0].code == "E-041"

    def test_rejects_an_error_code_for_a_machine_that_does_not_exist(self) -> None:
        payload = catalogue_data(
            error_codes=[
                {
                    "code": "E-017",
                    "title_en": "Squeegee lift fault",
                    "title_hu": "Lehuzo emelesi hiba",
                    "applies_to": ["XX-99"],
                }
            ]
        )
        with pytest.raises(ValidationError, match="unknown machine types"):
            Catalogue.model_validate(payload)

    def test_rejects_an_item_number_reused_across_machine_types(self) -> None:
        second = machine_data(
            code="SDR-90",
            variants=[variant_data(), variant_data("1.512-999.0")],
        )
        payload = catalogue_data(machine_types=[machine_data(), second])
        with pytest.raises(ValidationError, match="reused across machine types"):
            Catalogue.model_validate(payload)

    def test_machine_raises_for_an_unknown_code(self) -> None:
        catalogue = Catalogue.model_validate(catalogue_data())
        with pytest.raises(KeyError):
            catalogue.machine("XX-99")

    def test_errors_for_an_unrelated_machine_is_empty(self) -> None:
        catalogue = Catalogue.model_validate(catalogue_data())
        assert catalogue.errors_for("SDR-90") == ()


class TestCorpusSpec:
    def test_accepts_a_consistent_spec(self) -> None:
        spec = CorpusSpec.model_validate(spec_data())
        assert spec.total == 10
        assert spec.quota(DocumentType.GLOSSARY).total == 4

    def test_rejects_quotas_that_do_not_sum_to_the_total(self) -> None:
        with pytest.raises(ValidationError, match="quotas sum to 10 but total is 11"):
            CorpusSpec.model_validate(spec_data(total=11))

    def test_rejects_a_document_type_declared_twice(self) -> None:
        payload = spec_data(
            quotas=[
                {"type": "glossary", "en": 5},
                {"type": "glossary", "en": 5},
            ]
        )
        with pytest.raises(ValidationError, match="more than once"):
            CorpusSpec.model_validate(payload)

    def test_rejects_a_conversion_budget_larger_than_the_corpus(self) -> None:
        with pytest.raises(ValidationError, match="format subsets ask for"):
            CorpusSpec.model_validate(spec_data(pdf_count=9, scanned_pdf_count=5, docx_count=3))

    def test_quota_raises_for_a_type_the_spec_does_not_declare(self) -> None:
        spec = CorpusSpec.model_validate(spec_data())
        with pytest.raises(KeyError):
            spec.quota(DocumentType.FAULT_REPORT)


class TestTypeQuota:
    def test_counts_per_language(self) -> None:
        quota = TypeQuota.model_validate({"type": "handover_note", "en": 41, "hu": 6})
        assert quota.count(Language.EN) == 41
        assert quota.count(Language.HU) == 6
        assert quota.total == 47

    def test_languages_default_to_zero(self) -> None:
        quota = TypeQuota.model_validate({"type": "service_manual", "en": 5})
        assert quota.count(Language.HU) == 0
