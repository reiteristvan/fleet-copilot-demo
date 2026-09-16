"""The seed data actually shipped in ``data/`` is loadable and complete.

The models in :mod:`fleet_copilot.corpus.models` prove that *a* catalogue is
well-formed. These tests prove that *this* catalogue is, so that editing
``data/catalogue.yaml`` badly fails the gate rather than failing during
generation, and that the corpus still promises what ADR 0003 and the README say
it promises.
"""

from __future__ import annotations

from fleet_copilot.corpus.models import Language, MachineFamily
from fleet_copilot.corpus.seed import load_catalogue, load_corpus_spec


class TestShippedCatalogue:
    def test_loads(self) -> None:
        assert load_catalogue().machine_types

    def test_covers_every_machine_family_exactly_once(self) -> None:
        """Five families, five machine types -- the corpus spans the range."""
        families = [machine.family for machine in load_catalogue().machine_types]
        assert sorted(families) == sorted(MachineFamily)

    def test_every_machine_has_two_or_three_variants(self) -> None:
        for machine in load_catalogue().machine_types:
            assert 2 <= len(machine.variants) <= 3, machine.code

    def test_item_numbers_are_unique_across_the_whole_catalogue(self) -> None:
        items = [v.item_number for m in load_catalogue().machine_types for v in m.variants]
        assert len(set(items)) == len(items)

    def test_every_machine_is_named_in_both_languages(self) -> None:
        """A machine with the same name in both languages would make the
        Hungarian documents indistinguishable from the English ones on the one
        term most likely to be searched for."""
        for machine in load_catalogue().machine_types:
            assert machine.name_en != machine.name_hu, machine.code

    def test_every_machine_has_at_least_one_error_code(self) -> None:
        """Fault and service reports are keyed by error code, so a machine with
        none of them cannot appear in either type."""
        catalogue = load_catalogue()
        for machine in catalogue.machine_types:
            assert catalogue.errors_for(machine.code), machine.code

    def test_has_sites_in_both_languages(self) -> None:
        languages = {site.language for site in load_catalogue().sites}
        assert languages == {Language.EN, Language.HU}

    def test_hungarian_text_carries_its_accents(self) -> None:
        """Stripped accents would make Hungarian retrieval easier than it is.

        The two characters checked here are the ones a lossy encoding or a
        careless normalisation drops first, and they appear in the machine names
        and error titles that queries are most likely to contain.
        """
        catalogue = load_catalogue()
        hungarian = "".join(machine.name_hu for machine in catalogue.machine_types)
        hungarian += "".join(error.title_hu for error in catalogue.error_codes)
        assert "ő" in hungarian
        assert "ű" in hungarian or "ú" in hungarian


class TestShippedCorpusSpec:
    def test_loads_and_promises_one_hundred_and_twenty_documents(self) -> None:
        assert load_corpus_spec().total == 120

    def test_language_split_reaches_both_languages_in_meaningful_volume(self) -> None:
        """Enough Hungarian to measure against, not so much it stops being the
        minority language the cross-language case depends on."""
        spec = load_corpus_spec()
        hungarian = sum(quota.hu for quota in spec.quotas)
        assert 10 <= hungarian <= 20
        assert sum(quota.en for quota in spec.quotas) + hungarian == spec.total

    def test_the_format_subsets_leave_a_markdown_majority(self) -> None:
        spec = load_corpus_spec()
        converted = spec.pdf_count + spec.scanned_pdf_count + spec.docx_count
        assert converted == 25
        assert spec.total - converted == 95

    def test_some_pdfs_have_no_text_layer(self) -> None:
        """Without these, OCR is a pass-through rather than a path under test."""
        assert load_corpus_spec().scanned_pdf_count > 0

    def test_every_document_type_is_given_a_quota(self) -> None:
        """A type with no quota is a type the corpus silently does not contain."""
        spec = load_corpus_spec()
        assert all(quota.total > 0 for quota in spec.quotas)


class TestShippedGlossary:
    def test_defines_the_vocabulary_the_corpus_is_written_in(self) -> None:
        terms = {term.term_en for term in load_catalogue().glossary_terms}
        for expected in ("Squeegee", "Solution tank", "Recovery tank", "Brush deck"):
            assert expected in terms

    def test_covers_the_battery_vocabulary_the_planted_conflict_turns_on(self) -> None:
        """The planted conflict is about charging temperature, so a reader
        resolving it needs these two terms defined somewhere in the corpus."""
        terms = {term.term_en for term in load_catalogue().glossary_terms}
        assert {"C-rate", "Deep discharge"} <= terms

    def test_every_term_is_defined_in_both_languages(self) -> None:
        for term in load_catalogue().glossary_terms:
            assert term.term_en != term.term_hu, term.term_en
            assert term.definition_en != term.definition_hu, term.term_en
