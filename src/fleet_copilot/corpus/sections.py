"""Build the body of each document type from the catalogue and the fragments.

One function per document type. Each is handed the subject the document is
about, a seeded ``Random``, and the phrase banks for its type and language, and
returns the section tree :mod:`fleet_copilot.corpus.document` defines. Nothing
here touches the filesystem or decides which documents exist -- that is
:mod:`fleet_copilot.corpus.planner`.

The fleet-wide limits are module constants rather than catalogue fields. They
are the same figure everywhere by design: a manual, a maintenance procedure and
a service report quoting the brush wear limit must quote *one* number, because
the planted contradiction is a handover note disagreeing with that number, and
a corpus where the documents already disagreed by accident could not
demonstrate it.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from fleet_copilot.corpus.document import Block, Bullets, Paragraph, Section, Steps, Table
from fleet_copilot.corpus.models import (
    BatteryChemistry,
    Catalogue,
    ErrorCode,
    Language,
    MachineType,
    Site,
    Variant,
)
from fleet_copilot.corpus.seed import CorpusDataError, FragmentSet

BLADE_WEAR_LIMIT_MM = 3
"""Squeegee blade wear, measured back from the original profile."""

BRUSH_WEAR_LIMIT_MM = 12
"""Minimum bristle length. The number the planted contradiction disputes."""

MAX_CHARGE_TEMP_C = 40
"""Maximum pack surface temperature at which charging may begin."""

SUPERSEDED_MAX_CHARGE_TEMP_C = 45
"""The figure revision 3 of the battery-care procedure gave.

Kept as a constant beside the current limit so the planted conflict is visible
in the source rather than hidden in a data file: revision 3 and revision 4 of
one procedure disagree by exactly these two numbers.
"""

_HEADINGS: Mapping[str, tuple[str, str]] = {
    "intended_use": ("Intended use", "Rendeltetésszerű használat"),
    "controls": ("Controls", "Kezelőszervek"),
    "daily_operation": ("Daily operation", "Napi üzemeltetés"),
    "filling_emptying": ("Filling and emptying", "Feltöltés és ürítés"),
    "after_use": ("After use", "Használat után"),
    "cautions": ("Cautions", "Figyelmeztetések"),
    "scope": ("Scope", "Hatály"),
    "service_intervals": ("Service intervals", "Szervizciklusok"),
    "diagnostics": ("Diagnostics", "Hibakeresés"),
    "parts": ("Parts and consumables", "Alkatrészek és kopóalkatrészek"),
    "safety_notes": ("Safety", "Biztonság"),
    "purpose": ("Purpose", "Cél"),
    "tools": ("Tools required", "Szükséges szerszámok"),
    "procedure": ("Procedure", "Eljárás"),
    "limits": ("Limits and intervals", "Határértékek és ciklusok"),
    "notes": ("Notes", "Megjegyzések"),
    "hazard": ("Hazard", "Veszély"),
    "control_measures": ("Control measures", "Védőintézkedések"),
    "ppe": ("Personal protective equipment", "Egyéni védőeszköz"),
    "emergency": ("In an emergency", "Vészhelyzet esetén"),
    "shift_note": ("Shift note", "Műszakjegyzet"),
    "observations": ("Observations", "Észrevételek"),
    "actions": ("Actions taken", "Elvégzett műveletek"),
    "outstanding": ("Outstanding", "Elmaradt feladatok"),
    "findings": ("Findings", "Megállapítások"),
    "work_done": ("Work carried out", "Elvégzett munka"),
    "parts_used": ("Parts used", "Felhasznált alkatrészek"),
    "outcome": ("Outcome", "Eredmény"),
    "symptom": ("Reported symptom", "Bejelentett tünet"),
    "assessment": ("Site assessment", "Helyszíni értékelés"),
    "status": ("Status", "Állapot"),
    "codes": ("Codes", "Kódok"),
    "guidance": ("Guidance", "Útmutató"),
    "terms": ("Terms", "Kifejezések"),
}


def heading(key: str, language: Language) -> str:
    """Return the section heading for ``key`` in ``language``."""
    english, hungarian = _HEADINGS[key]
    return english if language is Language.EN else hungarian


@dataclass(frozen=True, slots=True)
class Subject:
    """What one document is about.

    Not every field is meaningful for every type -- a glossary has no machine
    and an operator manual has no serial -- so the builders take the fields they
    use and ignore the rest.
    """

    language: Language
    machine: MachineType | None = None
    variant: Variant | None = None
    site: Site | None = None
    serial: str | None = None
    error: ErrorCode | None = None
    hours: int | None = None
    operator: str | None = None
    max_charge_temp: int = MAX_CHARGE_TEMP_C

    def placeholders(self) -> dict[str, str]:
        """Return the substitution values this subject can actually supply.

        A value this subject does not have is *absent from the mapping* rather
        than present as a dash. A sweeper has no solution tank, and rendering
        "Fill the solution tank to — litres" for one would be a document that
        confidently describes a part the machine does not have. Absence instead
        means :meth:`can_fill` rejects the phrase and it is never selected.
        """
        machine = self.machine
        variant = self.variant
        values: dict[str, str] = {
            "blade_limit": str(BLADE_WEAR_LIMIT_MM),
            "brush_limit": str(BRUSH_WEAR_LIMIT_MM),
            "max_charge_temp": str(self.max_charge_temp),
        }
        if machine is not None:
            values["machine"] = machine.name(self.language)
            values["code"] = machine.code
        if variant is not None:
            values["item_number"] = variant.item_number
            values["deck"] = variant.deck.value
            values["battery"] = _battery_label(variant.battery)
            if variant.solution_tank_l is not None:
                values["solution_tank"] = str(variant.solution_tank_l)
            if variant.recovery_tank_l is not None:
                values["recovery_tank"] = str(variant.recovery_tank_l)
            if variant.hopper_l is not None:
                values["hopper"] = str(variant.hopper_l)
        if self.site is not None:
            values["site"] = self.site.name
        if self.serial is not None:
            values["serial"] = self.serial
        if self.error is not None:
            values["error_code"] = self.error.code
            values["error_title"] = self.error.title(self.language)
        if self.hours is not None:
            values["hours"] = str(self.hours)
        if self.operator is not None:
            values["operator"] = self.operator
        return values

    def can_fill(self, phrase: str) -> bool:
        """Whether every placeholder in ``phrase`` has a value for this subject."""
        return placeholders_in(phrase) <= self.placeholders().keys()


PLACEHOLDER_NAMES: Final = frozenset(
    {
        "machine",
        "code",
        "item_number",
        "deck",
        "battery",
        "solution_tank",
        "recovery_tank",
        "hopper",
        "site",
        "serial",
        "error_code",
        "error_title",
        "hours",
        "operator",
        "blade_limit",
        "brush_limit",
        "max_charge_temp",
    }
)
"""Every placeholder a fragment may use.

Because an unfillable phrase is silently skipped rather than rendered, a
*misspelled* placeholder would remove its phrase from the corpus without
complaint. The shipped fragments are checked against this set by the tests, so
the typo is caught at the gate instead of quietly shrinking a bank.
"""

_PLACEHOLDER_PATTERN: Final = re.compile(r"\{([a-z_]+)\}")


def placeholders_in(phrase: str) -> frozenset[str]:
    """Return the placeholder names ``phrase`` refers to."""
    return frozenset(_PLACEHOLDER_PATTERN.findall(phrase))


def _battery_label(chemistry: BatteryChemistry) -> str:
    """Render a battery chemistry the way a document would write it."""
    return "AGM" if chemistry is BatteryChemistry.AGM else "lithium-ion"


def fill(phrase: str, subject: Subject) -> str:
    """Substitute ``subject``'s values into ``phrase``.

    A phrase naming a placeholder that does not exist is a typo in a fragment
    file, and the error says which phrase so it can be found without grepping
    all nine of them.
    """
    try:
        return phrase.format(**subject.placeholders())
    except KeyError as error:
        msg = f"fragment refers to unknown placeholder {error}: {phrase!r}"
        raise CorpusDataError(msg) from error
    except (IndexError, ValueError) as error:
        msg = f"fragment has malformed placeholder syntax ({error}): {phrase!r}"
        raise CorpusDataError(msg) from error


def _usable(phrases: Sequence[str], subject: Subject) -> list[str]:
    """Return the phrases ``subject`` can supply every placeholder for.

    An empty result is a bug rather than an edge case: it means a whole bank is
    unusable for this document type, which would silently produce a section with
    no content.
    """
    usable = [phrase for phrase in phrases if subject.can_fill(phrase)]
    if not usable:
        msg = f"no phrase in this bank can be filled for {subject.language.value} subject"
        raise CorpusDataError(msg)
    return usable


def _some(rng: random.Random, phrases: Sequence[str], count: int) -> tuple[str, ...]:
    """Draw ``count`` distinct phrases, or all of them if the bank is smaller."""
    return tuple(rng.sample(list(phrases), min(count, len(phrases))))


def _filled(
    rng: random.Random, phrases: Sequence[str], count: int, subject: Subject
) -> tuple[str, ...]:
    """Draw ``count`` fillable phrases and substitute ``subject`` into each."""
    return tuple(fill(phrase, subject) for phrase in _some(rng, _usable(phrases, subject), count))


def _one(rng: random.Random, phrases: Sequence[str], subject: Subject) -> str:
    """Draw a single fillable phrase and substitute ``subject`` into it."""
    return fill(rng.choice(_usable(phrases, subject)), subject)


def operator_manual(subject: Subject, rng: random.Random, bank: FragmentSet) -> tuple[Section, ...]:
    """Build an operator manual: what the person driving the machine needs."""
    language = subject.language
    return (
        Section(
            heading=heading("intended_use", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("intended_use"), subject)),),
        ),
        Section(
            heading=heading("controls", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("controls"), 5, subject)),),
        ),
        Section(
            heading=heading("daily_operation", language),
            blocks=(Steps(items=_filled(rng, bank.bank("operation_steps"), 6, subject)),),
        ),
        Section(
            heading=heading("filling_emptying", language),
            blocks=(Steps(items=_filled(rng, bank.bank("filling_steps"), 4, subject)),),
        ),
        Section(
            heading=heading("after_use", language),
            blocks=(Steps(items=_filled(rng, bank.bank("after_use_steps"), 5, subject)),),
        ),
        Section(
            heading=heading("cautions", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("cautions"), 4, subject)),),
        ),
    )


def service_manual(subject: Subject, rng: random.Random, bank: FragmentSet) -> tuple[Section, ...]:
    """Build a service manual: deeper than the operator manual, same vocabulary."""
    language = subject.language
    intervals = Table(
        header=("Interval", "Task"),
        rows=(
            ("Daily", "Rinse both tanks, check squeegee blades, record hour meter"),
            ("50 h", "Measure brush bristle length against the wear limit"),
            ("250 h", "Inspect filter and seal, check deck height across the width"),
            ("500 h", "Full battery capacity check and charger profile verification"),
            ("1000 h", "Replace squeegee blade set and drive plate regardless of wear"),
        ),
    )
    return (
        Section(
            heading=heading("scope", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("scope"), subject)),),
        ),
        Section(heading=heading("service_intervals", language), blocks=(intervals,)),
        Section(
            heading=heading("diagnostics", language),
            blocks=(Steps(items=_filled(rng, bank.bank("diagnostic_steps"), 6, subject)),),
        ),
        Section(
            heading=heading("parts", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("parts_notes"), 4, subject)),),
        ),
        Section(
            heading=heading("safety_notes", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("safety_notes"), 3, subject)),),
        ),
    )


def maintenance_procedure(
    subject: Subject,
    rng: random.Random,
    bank: FragmentSet,
    *,
    procedure: str,
) -> tuple[Section, ...]:
    """Build one maintenance procedure, named by its step bank."""
    language = subject.language
    limits = Table(
        header=("Item", "Limit", "Interval"),
        rows=(
            ("Squeegee blade", f"{BLADE_WEAR_LIMIT_MM} mm wear", "Check daily"),
            ("Brush bristle", f"{BRUSH_WEAR_LIMIT_MM} mm minimum length", "Measure every 50 h"),
            ("Charging temperature", f"{subject.max_charge_temp} C maximum", "Before every charge"),
            ("Filter seal", "Replaces with filter", "Every 250 h"),
        ),
    )
    return (
        Section(
            heading=heading("purpose", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("purpose"), subject)),),
        ),
        Section(
            heading=heading("tools", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("tools"), 4, subject)),),
        ),
        Section(
            heading=heading("procedure", language),
            blocks=(
                Steps(
                    items=tuple(
                        fill(step, subject)
                        for step in _usable(bank.bank(f"steps_{procedure}"), subject)
                    )
                ),
            ),
        ),
        Section(heading=heading("limits", language), blocks=(limits,)),
        Section(
            heading=heading("notes", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("notes"), 2, subject)),),
        ),
    )


def safety_document(subject: Subject, rng: random.Random, bank: FragmentSet) -> tuple[Section, ...]:
    """Build a safety document."""
    language = subject.language
    return (
        Section(
            heading=heading("hazard", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("hazards"), subject)),),
        ),
        Section(
            heading=heading("control_measures", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("controls"), 5, subject)),),
        ),
        Section(
            heading=heading("ppe", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("ppe"), 3, subject)),),
        ),
        Section(
            heading=heading("emergency", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("emergency"), 3, subject)),),
        ),
    )


def handover_note(
    subject: Subject,
    rng: random.Random,
    bank: FragmentSet,
    *,
    extra: str | None = None,
) -> tuple[Section, ...]:
    """Build a shift handover note.

    ``extra`` appends one more observation. It is how the planted contradiction
    and one planted injection reach the corpus: buried among ordinary shift
    observations rather than standing alone as the document's only content,
    which is what makes finding them a retrieval problem.
    """
    language = subject.language
    observations = list(_filled(rng, bank.bank("observations"), 3, subject))
    if extra is not None:
        observations.insert(rng.randrange(len(observations) + 1), extra)
    return (
        Section(
            heading=heading("shift_note", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("openings"), subject)),),
        ),
        Section(
            heading=heading("observations", language),
            blocks=(Bullets(items=tuple(observations)),),
        ),
        Section(
            heading=heading("actions", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("actions_taken"), 2, subject)),),
        ),
        Section(
            heading=heading("outstanding", language),
            blocks=(
                Bullets(items=_filled(rng, bank.bank("outstanding"), 2, subject)),
                Paragraph(text=_one(rng, bank.bank("signoffs"), subject)),
            ),
        ),
    )


def service_report(
    subject: Subject,
    rng: random.Random,
    bank: FragmentSet,
    *,
    extra: str | None = None,
) -> tuple[Section, ...]:
    """Build an engineer's service report, keyed by serial and error code."""
    language = subject.language
    findings: list[Block] = [Paragraph(text=_one(rng, bank.bank("findings"), subject))]
    if extra is not None:
        findings.append(Paragraph(text=extra))
    return (
        Section(heading=heading("findings", language), blocks=tuple(findings)),
        Section(
            heading=heading("work_done", language),
            blocks=(Steps(items=_filled(rng, bank.bank("actions"), 3, subject)),),
        ),
        Section(
            heading=heading("parts_used", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("parts_used"), 2, subject)),),
        ),
        Section(
            heading=heading("outcome", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("outcomes"), subject)),),
        ),
    )


def fault_report(subject: Subject, rng: random.Random, bank: FragmentSet) -> tuple[Section, ...]:
    """Build a fault report raised by the site, before anyone has attended."""
    language = subject.language
    return (
        Section(
            heading=heading("symptom", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("symptoms"), subject)),),
        ),
        Section(
            heading=heading("assessment", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("diagnosis"), subject)),),
        ),
        Section(
            heading=heading("status", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("resolution"), subject)),),
        ),
    )


def error_code_reference(
    subject: Subject,
    rng: random.Random,
    bank: FragmentSet,
    catalogue: Catalogue,
) -> tuple[Section, ...]:
    """Build the error-code reference, rendered from the catalogue's own table."""
    language = subject.language
    codes = Table(
        header=("Code", "Meaning", "Applies to"),
        rows=tuple(
            (error.code, error.title(language), ", ".join(error.applies_to))
            for error in catalogue.error_codes
        ),
    )
    return (
        Section(
            heading=heading("codes", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("openings"), subject)), codes),
        ),
        Section(
            heading=heading("guidance", language),
            blocks=(Bullets(items=_filled(rng, bank.bank("guidance"), 2, subject)),),
        ),
    )


def glossary(
    subject: Subject,
    rng: random.Random,
    bank: FragmentSet,
    catalogue: Catalogue,
) -> tuple[Section, ...]:
    """Build the glossary, rendered from the catalogue's vocabulary."""
    language = subject.language
    terms = Table(
        header=("Term", "Definition"),
        rows=tuple(
            (term.term(language), term.definition(language)) for term in catalogue.glossary_terms
        ),
    )
    return (
        Section(
            heading=heading("terms", language),
            blocks=(Paragraph(text=_one(rng, bank.bank("openings"), subject)), terms),
        ),
    )
