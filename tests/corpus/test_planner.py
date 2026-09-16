"""The planner builds the corpus the spec promises, deterministically.

These run against the shipped seed data rather than fixtures. The planner's job
is to turn *this* catalogue and *this* spec into 120 documents with known
defects planted in known places, and a test using invented seed data would not
check that.
"""

from __future__ import annotations

from collections import Counter

import pytest

from fleet_copilot.corpus.models import (
    Catalogue,
    CorpusSpec,
    DocumentType,
    Language,
    OutputFormat,
    PlantedKind,
)
from fleet_copilot.corpus.planner import (
    CONFLICT_MACHINE_CODE,
    CONTRADICTION_TEXT,
    HUNGARIAN_MANUAL_CODES,
    INJECTION_TEXTS,
    PlannedDocument,
    plan_corpus,
)
from fleet_copilot.corpus.sections import (
    BRUSH_WEAR_LIMIT_MM,
    MAX_CHARGE_TEMP_C,
    PLACEHOLDER_NAMES,
    SUPERSEDED_MAX_CHARGE_TEMP_C,
    placeholders_in,
)
from fleet_copilot.corpus.seed import Fragments, load_catalogue, load_corpus_spec, load_fragments


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return load_catalogue()


@pytest.fixture(scope="module")
def spec() -> CorpusSpec:
    return load_corpus_spec()


@pytest.fixture(scope="module")
def fragments() -> Fragments:
    return load_fragments()


@pytest.fixture(scope="module")
def corpus(
    catalogue: Catalogue, spec: CorpusSpec, fragments: Fragments
) -> tuple[PlannedDocument, ...]:
    return plan_corpus(catalogue, spec, fragments)


def planted(corpus: tuple[PlannedDocument, ...], identifier: str) -> list[PlannedDocument]:
    """Return every document carrying the planted case ``identifier``."""
    return [doc for doc in corpus if doc.planted is not None and doc.planted.id == identifier]


class TestDeterminism:
    def test_the_same_seed_produces_the_same_corpus(
        self, catalogue: Catalogue, spec: CorpusSpec, fragments: Fragments
    ) -> None:
        """The property the committed manifest of content hashes depends on."""
        first = plan_corpus(catalogue, spec, fragments)
        second = plan_corpus(catalogue, spec, fragments)
        assert [doc.plan for doc in first] == [doc.plan for doc in second]
        assert [doc.output_format for doc in first] == [doc.output_format for doc in second]

    def test_a_different_seed_produces_a_different_corpus(
        self, catalogue: Catalogue, spec: CorpusSpec, fragments: Fragments
    ) -> None:
        """Confirms the seed is actually reaching the generator.

        Without this, a planner that ignored the seed entirely would pass the
        determinism test above perfectly.
        """
        other = spec.model_copy(update={"seed": spec.seed + 1})
        assert plan_corpus(catalogue, spec, fragments) != plan_corpus(catalogue, other, fragments)

    def test_documents_come_back_in_doc_id_order(self, corpus: tuple[PlannedDocument, ...]) -> None:
        identifiers = [doc.doc_id for doc in corpus]
        assert identifiers == sorted(identifiers)


class TestShape:
    def test_plans_exactly_the_declared_total(
        self, corpus: tuple[PlannedDocument, ...], spec: CorpusSpec
    ) -> None:
        assert len(corpus) == spec.total

    def test_matches_every_quota_in_both_languages(
        self, corpus: tuple[PlannedDocument, ...], spec: CorpusSpec
    ) -> None:
        for quota in spec.quotas:
            for language in (Language.EN, Language.HU):
                actual = sum(
                    1
                    for doc in corpus
                    if doc.plan.front_matter.type is quota.type and doc.plan.language is language
                )
                assert actual == quota.count(language), f"{quota.type.value}/{language.value}"

    def test_doc_ids_are_unique(self, corpus: tuple[PlannedDocument, ...]) -> None:
        identifiers = [doc.doc_id for doc in corpus]
        assert len(set(identifiers)) == len(identifiers)

    def test_format_counts_match_the_spec(
        self, corpus: tuple[PlannedDocument, ...], spec: CorpusSpec
    ) -> None:
        counts = Counter(doc.output_format for doc in corpus)
        assert counts[OutputFormat.PDF] == spec.pdf_count
        assert counts[OutputFormat.SCANNED_PDF] == spec.scanned_pdf_count
        assert counts[OutputFormat.DOCX] == spec.docx_count
        converted = spec.pdf_count + spec.scanned_pdf_count + spec.docx_count
        assert counts[OutputFormat.MARKDOWN] == spec.total - converted

    def test_no_pdf_is_hungarian(self, corpus: tuple[PlannedDocument, ...]) -> None:
        """The PDF core fonts carry no accented Hungarian characters."""
        pdfs = {OutputFormat.PDF, OutputFormat.SCANNED_PDF}
        assert not [d for d in corpus if d.output_format in pdfs and d.language is Language.HU]

    def test_at_least_one_docx_is_hungarian(self, corpus: tuple[PlannedDocument, ...]) -> None:
        """Otherwise the corpus is three formats in English and two languages in
        Markdown, which is not the same promise."""
        docx = [d for d in corpus if d.output_format is OutputFormat.DOCX]
        assert any(doc.language is Language.HU for doc in docx)

    def test_the_cross_language_manual_pairs_both_exist(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        identifiers = {doc.doc_id for doc in corpus}
        for code in HUNGARIAN_MANUAL_CODES:
            stem = code.lower()
            assert f"{stem}-operator-manual" in identifiers
            assert f"{stem}-operator-manual-hu" in identifiers


class TestPlantedCases:
    def test_plants_five_distinct_cases(self, corpus: tuple[PlannedDocument, ...]) -> None:
        assert {doc.planted.id for doc in corpus if doc.planted} == {
            "conflict-battery-charging-temp",
            "contradiction-brush-wear-limit",
            "injection-handover",
            "injection-service-report",
            "injection-scanned-pdf",
        }

    def test_plants_three_prompt_injections(self, corpus: tuple[PlannedDocument, ...]) -> None:
        injections = [
            doc
            for doc in corpus
            if doc.planted is not None and doc.planted.kind is PlantedKind.PROMPT_INJECTION
        ]
        assert len(injections) == 3
        assert len({doc.doc_id for doc in injections}) == 3

    def test_every_injection_text_reaches_its_document(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        for identifier, text in INJECTION_TEXTS:
            documents = planted(corpus, identifier)
            assert len(documents) == 1, identifier
            assert text in documents[0].plan.plain_text()

    def test_the_ocr_only_injection_is_in_a_scanned_pdf(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """It is the whole point of that case: the text must be unreachable
        without OCR, so it cannot be left to the format lottery."""
        documents = planted(corpus, "injection-scanned-pdf")
        assert [doc.output_format for doc in documents] == [OutputFormat.SCANNED_PDF]

    def test_the_conflicting_revisions_are_two_documents_that_disagree(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        documents = planted(corpus, "conflict-battery-charging-temp")
        assert len(documents) == 2
        revisions = sorted(doc.plan.front_matter.revision for doc in documents)
        assert revisions == [3, 4]

        by_revision = {doc.plan.front_matter.revision: doc.plan.plain_text() for doc in documents}
        assert f"{SUPERSEDED_MAX_CHARGE_TEMP_C} C" in by_revision[3]
        assert f"{MAX_CHARGE_TEMP_C} C" in by_revision[4]
        assert f"{SUPERSEDED_MAX_CHARGE_TEMP_C} C" not in by_revision[4]

    def test_the_superseded_revision_is_dated_earlier(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """A reader resolving the conflict needs something to resolve it *by*."""
        documents = planted(corpus, "conflict-battery-charging-temp")
        by_revision = {doc.plan.front_matter.revision: doc.plan.front_matter for doc in documents}
        assert by_revision[3].effective_date < by_revision[4].effective_date

    def test_only_the_conflicting_pair_uses_the_superseded_temperature(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """Every other battery-care procedure must agree, or the conflict is
        noise rather than a planted case."""
        for doc in corpus:
            if doc.plan.front_matter.revision == 3 and "battery-care" in doc.doc_id:
                continue
            assert f"{SUPERSEDED_MAX_CHARGE_TEMP_C} C" not in doc.plan.plain_text(), doc.doc_id

    def test_the_contradiction_disputes_the_limit_every_other_document_gives(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        documents = planted(corpus, "contradiction-brush-wear-limit")
        assert len(documents) == 1
        assert CONTRADICTION_TEXT in documents[0].plan.plain_text()
        assert documents[0].plan.front_matter.type is DocumentType.HANDOVER_NOTE

        others = [
            doc
            for doc in corpus
            if doc.plan.front_matter.type is DocumentType.MAINTENANCE_PROCEDURE
            and "brush-wear" in doc.doc_id
        ]
        assert others
        for doc in others:
            assert f"{BRUSH_WEAR_LIMIT_MM} mm" in doc.plan.plain_text()

    def test_no_document_carries_two_planted_cases(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """An eval retrieving such a document could not say which case it found."""
        carriers = [doc.doc_id for doc in corpus if doc.planted is not None]
        assert len(set(carriers)) == len(carriers)

    def test_planted_status_never_reaches_the_document(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """ADR 0003: a planted flag in indexed metadata would make every one of
        these cases solvable by a filter rather than by the pipeline."""
        for doc in corpus:
            serialised = doc.plan.front_matter.model_dump()
            assert "planted" not in serialised
            assert "planted" not in doc.plan.plain_text().lower()


class TestSubstitution:
    def test_no_placeholder_survives_into_a_document(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        for doc in corpus:
            text = doc.plan.plain_text()
            assert "{" not in text and "}" not in text, doc.doc_id

    def test_every_fragment_placeholder_is_one_the_planner_supplies(
        self, fragments: Fragments
    ) -> None:
        """An unfillable phrase is skipped rather than rendered, so a misspelled
        placeholder would silently shrink a bank instead of failing."""
        for document_type, file in fragments.files.items():
            for language_bank in (file.en, file.hu):
                if language_bank is None:
                    continue
                for name, phrases in language_bank.banks.items():
                    for phrase in phrases:
                        unknown = placeholders_in(phrase) - PLACEHOLDER_NAMES
                        assert not unknown, f"{document_type.value}/{name}: {sorted(unknown)}"

    def test_a_sweeper_is_never_told_about_a_solution_tank(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        """The reason unfillable phrases are skipped rather than dashed out."""
        for doc in corpus:
            for code in doc.plan.front_matter.machine_types:
                machine = catalogue.machine(code)
                if machine.variants[0].solution_tank_l is None:
                    assert "solution tank to" not in doc.plan.plain_text(), doc.doc_id

    def test_a_scrubber_is_never_told_about_a_hopper_capacity(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        for doc in corpus:
            for code in doc.plan.front_matter.machine_types:
                machine = catalogue.machine(code)
                if machine.variants[0].hopper_l is None:
                    assert "litre hopper" not in doc.plan.plain_text(), doc.doc_id


class TestProcedureApplicability:
    def test_only_scrubber_dryers_get_a_squeegee_blade_procedure(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        """A squeegee procedure for a sweeper documents a part it does not have."""
        for doc in corpus:
            if "maint-squeegee-blade-change" not in doc.doc_id:
                continue
            for code in doc.plan.front_matter.machine_types:
                assert "scrubber_dryer" in catalogue.machine(code).family.value

    def test_the_conflict_machine_has_both_battery_care_revisions(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        stem = CONFLICT_MACHINE_CODE.lower()
        identifiers = {doc.doc_id for doc in corpus}
        assert f"{stem}-maint-battery-care-rev3" in identifiers
        assert f"{stem}-maint-battery-care-rev4" in identifiers
        assert f"{stem}-maint-battery-care" not in identifiers


class TestSerials:
    def test_a_serial_is_shared_between_reports_about_one_machine(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        """Fault and service reports must join on the serial, or the corpus has
        no multi-document questions in it."""
        service = {
            serial
            for doc in corpus
            if doc.plan.front_matter.type is DocumentType.SERVICE_REPORT
            for serial in doc.plan.front_matter.serials
        }
        faults = {
            serial
            for doc in corpus
            if doc.plan.front_matter.type is DocumentType.FAULT_REPORT
            for serial in doc.plan.front_matter.serials
        }
        assert service & faults

    def test_every_serial_belongs_to_the_machine_it_is_filed_under(
        self, corpus: tuple[PlannedDocument, ...]
    ) -> None:
        for doc in corpus:
            for serial in doc.plan.front_matter.serials:
                stem = serial.split("-")[0]
                codes = {code.replace("-", "") for code in doc.plan.front_matter.machine_types}
                assert stem in codes, doc.doc_id


class TestOperatorManualsCoverEveryVariant:
    def test_a_manual_lists_every_item_number_of_its_machine(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        """A manual covers a machine type, so a reader filtering on any one of
        its item numbers has to find it."""
        manuals = [
            doc for doc in corpus if doc.plan.front_matter.type is DocumentType.OPERATOR_MANUAL
        ]
        assert manuals
        for manual in manuals:
            code = manual.plan.front_matter.machine_types[0]
            expected = {variant.item_number for variant in catalogue.machine(code).variants}
            assert set(manual.plan.front_matter.item_numbers) == expected, manual.doc_id

    def test_a_manual_tabulates_each_variant_separately(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        """The figures differ by item number -- AGM and lithium take different
        charging regimes, the pad deck carries a smaller tank -- so a question
        about one variant must find the right row, not just the right document."""
        for doc in corpus:
            if doc.plan.front_matter.type is not DocumentType.OPERATOR_MANUAL:
                continue
            code = doc.plan.front_matter.machine_types[0]
            text = doc.plan.plain_text()
            for variant in catalogue.machine(code).variants:
                assert variant.item_number in text, f"{doc.doc_id}/{variant.item_number}"

    def test_a_manual_states_the_capacity_its_family_is_measured_by(
        self, corpus: tuple[PlannedDocument, ...], catalogue: Catalogue
    ) -> None:
        for doc in corpus:
            if doc.plan.front_matter.type is not DocumentType.OPERATOR_MANUAL:
                continue
            machine = catalogue.machine(doc.plan.front_matter.machine_types[0])
            text = doc.plan.plain_text()
            for variant in machine.variants:
                if variant.solution_tank_l is not None:
                    assert str(variant.solution_tank_l) in text, doc.doc_id
                elif variant.hopper_l is not None:
                    assert str(variant.hopper_l) in text, doc.doc_id
