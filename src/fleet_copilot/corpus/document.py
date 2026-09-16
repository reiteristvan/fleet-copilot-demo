"""The shape of a planned document, before it is written to any format.

The planner decides *what* a document says and the renderers decide how it
looks on the page. This module is the contract between them: a tree of headings
and typed blocks that Markdown, PDF and DOCX all render from. Going through one
intermediate rather than rendering Markdown and then parsing it back is what
keeps the three formats from drifting apart -- a table that renders correctly in
Markdown and loses its last column in DOCX is exactly the bug that would
otherwise be found by a human reading a PDF three epics from now.

:class:`FrontMatter` is the published half of ADR 0003: retrieval filters on
these keys, agents cite them and evals assert against them.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleet_copilot.corpus.models import DocumentType, Language

NonEmptyStr = Annotated[str, Field(min_length=1)]

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
"""A lowercase hyphenated identifier.

``doc_id`` is one of these because it becomes a blob name and the prefix of
every ``chunk_id`` derived from the document. A ``doc_id`` containing a slash
would silently create a blob path segment, and one containing a space would not
survive the round trip back from the blob name.
"""

Serial = Annotated[str, Field(pattern=r"^[A-Z0-9]{4,10}-\d{4}-\d{5}$")]
"""A machine serial number, ``SD50B-2026-01042``."""

ItemNumber = Annotated[str, Field(pattern=r"^\d\.\d{3}-\d{3}\.\d$")]
MachineCode = Annotated[str, Field(pattern=r"^[A-Z]{2,4}-\d{2,3}[A-Z]?$")]


class Paragraph(BaseModel):
    """A run of prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["paragraph"] = "paragraph"
    text: NonEmptyStr


class Bullets(BaseModel):
    """An unordered list, where order carries no meaning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bullets"] = "bullets"
    items: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


class Steps(BaseModel):
    """A numbered procedure, where order is the content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["steps"] = "steps"
    items: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


class Table(BaseModel):
    """A grid -- service intervals, wear limits, error codes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["table"] = "table"
    header: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    rows: Annotated[tuple[tuple[str, ...], ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _rows_match_the_header(self) -> Self:
        """Reject a row whose cell count differs from the header's.

        The three renderers disagree about ragged tables: Markdown drops the
        extra cells, python-docx raises, and the PDF writer lays the row out
        over the wrong columns. Rejecting it here means the disagreement cannot
        reach a point where one format is right and the others are quietly wrong.
        """
        width = len(self.header)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                msg = f"row {index} has {len(row)} cells but the header has {width}"
                raise ValueError(msg)
        return self


Block = Annotated[Paragraph | Bullets | Steps | Table, Field(discriminator="kind")]
"""One piece of content inside a section."""


class Section(BaseModel):
    """A heading and the blocks under it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    heading: NonEmptyStr
    blocks: Annotated[tuple[Block, ...], Field(min_length=1)]


class FrontMatter(BaseModel):
    """The metadata block at the top of every document.

    These keys and no others, per ADR 0003. Note what is absent: nothing here
    marks a document as carrying a planted conflict or injection. That lives in
    the manifest, because a flag in the front matter would be indexed with the
    rest of the document and make every planted case solvable by a metadata
    filter instead of by the pipeline under test.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Slug
    type: DocumentType
    machine_types: tuple[MachineCode, ...] = ()
    item_numbers: tuple[ItemNumber, ...] = ()
    serials: tuple[Serial, ...] = ()
    site: Slug | None = None
    language: Language
    revision: Annotated[int, Field(ge=1)]
    effective_date: date

    @field_validator("machine_types", "item_numbers", "serials", mode="after")
    @classmethod
    def _sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Normalise a reference list to sorted, deduplicated order.

        Front matter is serialised into the document bytes, so the order of
        these lists is part of the content hash. Leaving it to the order the
        planner happened to append in would make the corpus reproducible only by
        accident, and the manifest would record hashes that drift between runs
        for a reason nobody can see in a diff.
        """
        return tuple(sorted(set(value)))


class DocumentPlan(BaseModel):
    """A complete document, decided but not yet written to any format."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    front_matter: FrontMatter
    title: NonEmptyStr
    sections: Annotated[tuple[Section, ...], Field(min_length=1)]

    @property
    def doc_id(self) -> str:
        """Shorthand for the document's identifier."""
        return self.front_matter.doc_id

    @property
    def language(self) -> Language:
        """Shorthand for the document's language."""
        return self.front_matter.language

    def plain_text(self) -> str:
        """Return every word of the document, with no markup.

        Used by the tests that assert a planted injection is present in the
        content and that no planted marker leaked out of the manifest into the
        document itself.
        """
        parts: list[str] = [self.title]
        for section in self.sections:
            parts.append(section.heading)
            for block in section.blocks:
                match block:
                    case Paragraph():
                        parts.append(block.text)
                    case Bullets() | Steps():
                        parts.extend(block.items)
                    case Table():
                        parts.extend(block.header)
                        parts.extend(cell for row in block.rows for cell in row)
        return "\n".join(parts)
