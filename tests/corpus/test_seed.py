"""Seed data is validated at load, and every failure names the file it came from."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.corpus.seed import (
    CorpusDataError,
    FragmentFile,
    Fragments,
    FragmentSet,
    load_catalogue,
    load_corpus_spec,
    load_fragments,
)

CATALOGUE_YAML = """
machine_types:
  - code: SD-50B
    family: scrubber_dryer_walk_behind
    name_en: Walk-behind scrubber-dryer SD-50B
    name_hu: Kezi vezetesu surolo-szaritogep SD-50B
    variants:
      - item_number: "1.512-340.0"
        battery: agm
        deck: brush
        solution_tank_l: 50
        recovery_tank_l: 55
      - item_number: "1.512-341.0"
        battery: lithium-ion
        deck: pad
        solution_tank_l: 50
        recovery_tank_l: 55
error_codes:
  - code: E-041
    title_en: Battery over-temperature
    title_hu: Akkumulator tulmelegedes
    applies_to: [SD-50B]
sites:
  - slug: depot-north
    name: Depot North
    language: en
glossary_terms:
  - term_en: Squeegee
    term_hu: Lehuzogumi
    definition_en: The rubber blade that wipes solution into the suction path.
    definition_hu: A gumipenge, amely az oldatot a szivocsatornaba tereli.
"""

SPEC_YAML = """
seed: 20260916
total: 6
quotas:
  - type: glossary
    en: 3
    hu: 3
pdf_count: 1
scanned_pdf_count: 1
docx_count: 1
"""

FRAGMENT_YAML = """
type: glossary
en:
  banks:
    openings:
      - The following terms are used throughout this document.
hu:
  banks:
    openings:
      - Az alabbi kifejezesek szerepelnek a dokumentumban.
"""


def write(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` and return it."""
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadCatalogue:
    def test_loads_a_valid_catalogue(self, tmp_path: Path) -> None:
        catalogue = load_catalogue(write(tmp_path / "catalogue.yaml", CATALOGUE_YAML))
        assert catalogue.machine("SD-50B").name(Language.HU).startswith("Kezi")

    def test_reports_a_missing_file_with_its_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.yaml"
        with pytest.raises(CorpusDataError, match="cannot read"):
            load_catalogue(missing)

    def test_reports_malformed_yaml_with_its_path(self, tmp_path: Path) -> None:
        path = write(tmp_path / "catalogue.yaml", "machine_types: [unclosed\n")
        with pytest.raises(CorpusDataError, match="is not valid YAML"):
            load_catalogue(path)

    def test_rejects_a_top_level_list(self, tmp_path: Path) -> None:
        path = write(tmp_path / "catalogue.yaml", "- one\n- two\n")
        with pytest.raises(CorpusDataError, match="must contain a mapping"):
            load_catalogue(path)

    def test_reports_a_validation_failure_with_its_path(self, tmp_path: Path) -> None:
        broken = CATALOGUE_YAML.replace("applies_to: [SD-50B]", "applies_to: [XX-99]")
        path = write(tmp_path / "catalogue.yaml", broken)
        with pytest.raises(CorpusDataError, match="is not a valid Catalogue"):
            load_catalogue(path)


class TestLoadCorpusSpec:
    def test_loads_a_valid_spec(self, tmp_path: Path) -> None:
        spec = load_corpus_spec(write(tmp_path / "corpus_spec.yaml", SPEC_YAML))
        assert spec.total == 6
        assert spec.quota(DocumentType.GLOSSARY).hu == 3

    def test_reports_an_inconsistent_spec(self, tmp_path: Path) -> None:
        path = write(tmp_path / "corpus_spec.yaml", SPEC_YAML.replace("total: 6", "total: 7"))
        with pytest.raises(CorpusDataError, match="is not a valid CorpusSpec"):
            load_corpus_spec(path)


class TestFragmentSet:
    def test_bank_returns_its_phrases(self) -> None:
        fragments = FragmentSet.model_validate({"banks": {"openings": ["One.", "Two."]}})
        assert fragments.bank("openings") == ("One.", "Two.")

    def test_unknown_bank_lists_the_available_ones(self) -> None:
        fragments = FragmentSet.model_validate({"banks": {"openings": ["One."]}})
        with pytest.raises(KeyError, match="available: \\['openings'\\]"):
            fragments.bank("closings")

    def test_rejects_an_empty_bank(self) -> None:
        """An empty bank yields an empty phrase at generation time, not an error."""
        with pytest.raises(ValueError, match="at least 1 item"):
            FragmentSet.model_validate({"banks": {"openings": []}})


class TestFragmentFile:
    def test_refuses_to_fall_back_to_english_when_hungarian_is_absent(self) -> None:
        file = FragmentFile.model_validate(
            {"type": "glossary", "en": {"banks": {"openings": ["One."]}}}
        )
        assert file.for_language(Language.EN).bank("openings") == ("One.",)
        with pytest.raises(CorpusDataError, match="no Hungarian fragments"):
            file.for_language(Language.HU)


class TestLoadFragments:
    def test_loads_a_directory_of_fragment_files(self, tmp_path: Path) -> None:
        write(tmp_path / "glossary.yaml", FRAGMENT_YAML)
        fragments = load_fragments(tmp_path)
        assert fragments.of(DocumentType.GLOSSARY, Language.HU).bank("openings")[0].startswith("Az")

    def test_rejects_a_file_whose_type_disagrees_with_its_name(self, tmp_path: Path) -> None:
        write(tmp_path / "service_manual.yaml", FRAGMENT_YAML)
        with pytest.raises(CorpusDataError, match="declares type 'glossary' but is named"):
            load_fragments(tmp_path)

    def test_rejects_a_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(CorpusDataError, match="does not exist"):
            load_fragments(tmp_path / "absent")

    def test_rejects_an_empty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(CorpusDataError, match="no fragment files found"):
            load_fragments(tmp_path)

    def test_unknown_document_type_is_reported(self, tmp_path: Path) -> None:
        write(tmp_path / "glossary.yaml", FRAGMENT_YAML)
        fragments = load_fragments(tmp_path)
        with pytest.raises(CorpusDataError, match="no fragment file for handover_note"):
            fragments.of(DocumentType.HANDOVER_NOTE, Language.EN)


class TestFragments:
    def test_of_reaches_the_requested_language(self) -> None:
        file = FragmentFile.model_validate(
            {
                "type": "glossary",
                "en": {"banks": {"openings": ["English."]}},
                "hu": {"banks": {"openings": ["Magyar."]}},
            }
        )
        fragments = Fragments(files={DocumentType.GLOSSARY: file})
        assert fragments.of(DocumentType.GLOSSARY, Language.EN).bank("openings") == ("English.",)
        assert fragments.of(DocumentType.GLOSSARY, Language.HU).bank("openings") == ("Magyar.",)
