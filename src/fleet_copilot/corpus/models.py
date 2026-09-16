"""The seed data every document is assembled from.

``data/catalogue.yaml`` supplies the nouns -- machine types, configuration
variants, error codes, sites -- and ``data/corpus_spec.yaml`` declares how many
documents of each type and language to build from them. Both are parsed into
the models here before anything is generated, so a typo in the seed data fails
at load with a path to the offending field rather than surfacing 120 documents
later as prose that quietly contradicts itself.

Every collection is a tuple: these models are frozen, and a list field would
make them unhashable while still allowing the contents to be mutated behind the
generator's back.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]

ItemNumber = Annotated[str, Field(pattern=r"^\d\.\d{3}-\d{3}\.\d$")]
"""A spare-parts item number, ``1.512-340.0``.

Matching the manufacturer's real format matters for retrieval: the dots and
dashes are exactly the characters a tokenizer is most likely to split on, so a
corpus that used a simpler shape would not exercise the case that breaks.
"""

MachineCode = Annotated[str, Field(pattern=r"^[A-Z]{2,4}-\d{2,3}[A-Z]?$")]
"""A machine type code, ``SD-50B``."""

ErrorCodeStr = Annotated[str, Field(pattern=r"^E-\d{3}$")]
"""An error code as it appears on the machine display, ``E-041``."""


class Language(StrEnum):
    """The two languages the corpus is written in."""

    EN = "en"
    HU = "hu"


class MachineFamily(StrEnum):
    """The five machine families the catalogue covers."""

    SCRUBBER_DRYER_WALK_BEHIND = "scrubber_dryer_walk_behind"
    SCRUBBER_DRYER_RIDE_ON = "scrubber_dryer_ride_on"
    SWEEPER_RIDE_ON = "sweeper_ride_on"
    VACUUM_SWEEPER = "vacuum_sweeper"
    SINGLE_DISC = "single_disc"


class BatteryChemistry(StrEnum):
    """Battery chemistry, which drives most of the planted charging content."""

    LITHIUM_ION = "lithium-ion"
    AGM = "agm"


class DeckType(StrEnum):
    """What the machine carries under the deck."""

    BRUSH = "brush"
    PAD = "pad"


class DocumentType(StrEnum):
    """The document types the corpus contains."""

    OPERATOR_MANUAL = "operator_manual"
    SERVICE_MANUAL = "service_manual"
    MAINTENANCE_PROCEDURE = "maintenance_procedure"
    SAFETY_DOCUMENT = "safety_document"
    HANDOVER_NOTE = "handover_note"
    SERVICE_REPORT = "service_report"
    FAULT_REPORT = "fault_report"
    ERROR_CODE_REFERENCE = "error_code_reference"
    GLOSSARY = "glossary"


class OutputFormat(StrEnum):
    """How a document is represented in the uploaded set.

    A document has exactly one of these. ADR 0003: a converted document replaces
    its Markdown rather than accompanying it, so the corpus never contains an
    exact-duplicate pair nobody asked for.
    """

    MARKDOWN = "markdown"
    PDF = "pdf"
    SCANNED_PDF = "scanned_pdf"
    DOCX = "docx"


class PlantedKind(StrEnum):
    """The kinds of deliberate test case planted in the corpus."""

    CONFLICTING_REVISION = "conflicting_revision"
    HANDOVER_CONTRADICTION = "handover_contradiction"
    PROMPT_INJECTION = "prompt_injection"


class Variant(BaseModel):
    """One orderable configuration of a machine type."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    item_number: ItemNumber
    battery: BatteryChemistry
    deck: DeckType
    solution_tank_l: Annotated[int, Field(gt=0)] | None = None
    recovery_tank_l: Annotated[int, Field(gt=0)] | None = None
    hopper_l: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _recovery_tank_holds_the_solution_tank(self) -> Self:
        """Reject a recovery tank smaller than the solution tank.

        The recovery tank takes back everything the solution tank puts down plus
        the soil lifted with it, so on a real machine it is never the smaller of
        the two. A catalogue that got this backwards would generate maintenance
        text telling an operator to overflow the machine, and the mistake would
        be visible only to someone who knows the domain -- by which point it is
        in 120 documents and in the eval baselines built on them.

        Both capacities are optional because a sweeper carries a hopper and no
        tanks at all; which capacities a machine must declare is a question
        about its family, and is answered in :class:`MachineType`.
        """
        if self.solution_tank_l is None or self.recovery_tank_l is None:
            return self
        if self.recovery_tank_l < self.solution_tank_l:
            msg = (
                f"recovery tank ({self.recovery_tank_l} l) is smaller than the "
                f"solution tank ({self.solution_tank_l} l)"
            )
            raise ValueError(msg)
        return self


class MachineType(BaseModel):
    """A machine type, with the two or three variants it is sold in."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    code: MachineCode
    family: MachineFamily
    name_en: NonEmptyStr
    name_hu: NonEmptyStr
    variants: Annotated[tuple[Variant, ...], Field(min_length=2, max_length=3)]

    @model_validator(mode="after")
    def _item_numbers_are_unique(self) -> Self:
        """Reject a machine type that lists the same item number twice.

        An item number is how a document names one specific configuration, so a
        duplicate makes two variants indistinguishable in retrieval: a question
        about the lithium model becomes answerable from the AGM model's text
        with nothing in the citation to show which one was used.
        """
        seen = [variant.item_number for variant in self.variants]
        if len(set(seen)) != len(seen):
            msg = f"{self.code} repeats an item number: {sorted(seen)}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _variants_declare_the_capacities_their_family_uses(self) -> Self:
        """Reject a machine whose variants omit the capacity its family works by.

        A scrubber-dryer is described by its two tanks and a sweeper by its
        hopper, and documents quote those figures directly -- tank capacities in
        the filling procedure, hopper capacity in the emptying interval. A
        variant missing the one its family uses does not fail generation; it
        produces a manual with a blank where the number should be, which reads
        as a formatting bug rather than as missing seed data.
        """
        needs_tanks = self.family in {
            MachineFamily.SCRUBBER_DRYER_WALK_BEHIND,
            MachineFamily.SCRUBBER_DRYER_RIDE_ON,
        }
        needs_hopper = self.family in {
            MachineFamily.SWEEPER_RIDE_ON,
            MachineFamily.VACUUM_SWEEPER,
        }
        for variant in self.variants:
            if needs_tanks and (variant.solution_tank_l is None or variant.recovery_tank_l is None):
                msg = f"{self.code} is a scrubber-dryer, so {variant.item_number} needs both tanks"
                raise ValueError(msg)
            if needs_hopper and variant.hopper_l is None:
                msg = f"{self.code} is a sweeper, so {variant.item_number} needs a hopper capacity"
                raise ValueError(msg)
        return self

    def name(self, language: Language) -> str:
        """Return the machine's display name in ``language``."""
        return self.name_en if language is Language.EN else self.name_hu


class ErrorCode(BaseModel):
    """A fault the machine reports on its display."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    code: ErrorCodeStr
    title_en: NonEmptyStr
    title_hu: NonEmptyStr
    applies_to: Annotated[tuple[MachineCode, ...], Field(min_length=1)]

    def title(self, language: Language) -> str:
        """Return the fault's title in ``language``."""
        return self.title_en if language is Language.EN else self.title_hu


class Site(BaseModel):
    """A customer site machines are deployed to and handover notes written at."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
    name: NonEmptyStr
    language: Language


class Catalogue(BaseModel):
    """The whole of ``data/catalogue.yaml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    machine_types: Annotated[tuple[MachineType, ...], Field(min_length=1)]
    error_codes: Annotated[tuple[ErrorCode, ...], Field(min_length=1)]
    sites: Annotated[tuple[Site, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _cross_references_resolve(self) -> Self:
        """Reject machine codes and item numbers that do not line up.

        An error code naming a machine the catalogue does not contain would
        generate fault reports for a machine that appears nowhere else, which
        reads as a retrieval miss rather than as the bad data it is. Item
        numbers duplicated across machine types cause the same confusion that
        :meth:`MachineType._item_numbers_are_unique` catches within one.
        """
        known = {machine.code for machine in self.machine_types}
        for error in self.error_codes:
            unknown = sorted(set(error.applies_to) - known)
            if unknown:
                msg = f"error code {error.code} applies_to unknown machine types: {unknown}"
                raise ValueError(msg)

        items = [v.item_number for m in self.machine_types for v in m.variants]
        if len(set(items)) != len(items):
            duplicates = sorted({i for i in items if items.count(i) > 1})
            msg = f"item numbers are reused across machine types: {duplicates}"
            raise ValueError(msg)
        return self

    def machine(self, code: str) -> MachineType:
        """Return the machine type with ``code``, or raise :class:`KeyError`."""
        for machine in self.machine_types:
            if machine.code == code:
                return machine
        raise KeyError(code)

    def errors_for(self, code: str) -> tuple[ErrorCode, ...]:
        """Return every error code that applies to machine ``code``."""
        return tuple(error for error in self.error_codes if code in error.applies_to)


class TypeQuota(BaseModel):
    """How many documents of one type to write, per language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: DocumentType
    en: Annotated[int, Field(ge=0)] = 0
    hu: Annotated[int, Field(ge=0)] = 0

    @property
    def total(self) -> int:
        """The number of documents this quota accounts for."""
        return self.en + self.hu

    def count(self, language: Language) -> int:
        """Return the quota for ``language``."""
        return self.en if language is Language.EN else self.hu


class CorpusSpec(BaseModel):
    """The whole of ``data/corpus_spec.yaml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int
    total: Annotated[int, Field(gt=0)]
    quotas: Annotated[tuple[TypeQuota, ...], Field(min_length=1)]
    pdf_count: Annotated[int, Field(ge=0)]
    scanned_pdf_count: Annotated[int, Field(ge=0)]
    docx_count: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _quotas_are_consistent(self) -> Self:
        """Reject a spec whose parts do not add up to its declared total.

        ``total`` is what the corpus promises and the quotas are how it is
        delivered; letting them drift apart is how the count in the README, the
        ADR and the manifest stop agreeing with each other without anyone
        noticing. The format subsets are checked against the same total because
        a conversion budget larger than the corpus cannot be satisfied, and that
        failure would otherwise appear only at conversion time, after the whole
        corpus has already been generated.
        """
        declared = [quota.type for quota in self.quotas]
        if len(set(declared)) != len(declared):
            msg = "a document type appears in quotas more than once"
            raise ValueError(msg)

        counted = sum(quota.total for quota in self.quotas)
        if counted != self.total:
            msg = f"quotas sum to {counted} but total is {self.total}"
            raise ValueError(msg)

        converted = self.pdf_count + self.scanned_pdf_count + self.docx_count
        if converted > self.total:
            msg = f"format subsets ask for {converted} documents but the corpus has {self.total}"
            raise ValueError(msg)
        return self

    def quota(self, document_type: DocumentType) -> TypeQuota:
        """Return the quota for ``document_type``, or raise :class:`KeyError`."""
        for quota in self.quotas:
            if quota.type is document_type:
                return quota
        raise KeyError(document_type)
