"""What a parsed document is, independently of what parsed it.

One ``content`` string, and blocks that are spans into it. Nothing else holds
text. Three chunking strategies will slice this, and they slice one coordinate
system rather than each re-deriving offsets and getting it differently wrong.

All three parsers produce this same shape, so a chunker cannot tell a Markdown
document from an OCR'd one -- which is what makes a strategy comparison across
the whole corpus mean anything.

Deliberately free of any SDK import: this module is the contract, and the things
that import it -- tests, chunkers, the eval harness -- must not need an Azure
account to read it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ParseError(RuntimeError):
    """Raised when a document cannot be parsed honestly."""


class ParserId(StrEnum):
    """Which implementation produced a document."""

    AZURE_LAYOUT = "azure-document-intelligence"
    MARKDOWN = "markdown"
    LOCAL = "local"


class BlockRole(StrEnum):
    """What a span of content is.

    Seven of these mirror Document Intelligence's paragraph roles one for one,
    including the three kinds of page furniture. Those are kept rather than
    stripped: removing them would shift every later offset, and a chunker that
    wants to skip them can filter on the role.

    There is deliberately no LIST_ITEM. The service has no list role, so a
    Markdown parser that emitted one would produce output the Azure parser could
    not match, and a chunker would behave differently depending on which parser
    ran. ADR 0006 keeps step lists atomic by reading Markdown ordered-list syntax
    out of ``content``, which both parsers produce identically.
    """

    TITLE = "title"
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    FORMULA_BLOCK = "formula_block"


HEADING_ROLES: frozenset[BlockRole] = frozenset({BlockRole.TITLE, BlockRole.SECTION_HEADING})
"""Roles a breadcrumb is built from. The contextual-header strategy reads this."""


class ParsedPage(BaseModel):
    """One page, and the slice of content it accounts for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_number: Annotated[int, Field(ge=1)]
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]


class ParsedBlock(BaseModel):
    """A span of content, and what kind of thing it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: BlockRole
    text: NonEmptyStr
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    page_number: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _span_matches_text(self) -> Self:
        """Keep the span and the text in agreement.

        Chunkers slice ``content`` by offset and never read ``text``; a block
        whose two disagree produces a chunk that looks right in the model and
        wrong in the index, and nothing between here and a human reading a
        citation would notice.
        """
        if self.end <= self.start:
            msg = f"end ({self.end}) must be greater than start ({self.start})"
            raise ValueError(msg)
        if self.end - self.start != len(self.text):
            msg = (
                f"span {self.start}:{self.end} covers {self.end - self.start} characters "
                f"but text is {len(self.text)} characters long"
            )
            raise ValueError(msg)
        return self


class ParsedDocument(BaseModel):
    """A document after parsing, before it is chunked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: NonEmptyStr
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    parser: ParserId
    model_id: NonEmptyStr
    api_version: str | None = None
    content: NonEmptyStr
    blocks: tuple[ParsedBlock, ...]
    pages: tuple[ParsedPage, ...]
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("parsed_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        """Reject naive timestamps, matching Document.ingested_at.

        A parse may run on a laptop in one timezone and in CI in another; a naive
        timestamp cannot be ordered against one from the other without guessing
        its offset.
        """
        if value.tzinfo is None:
            msg = "parsed_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _blocks_partition_the_content_they_cover(self) -> Self:
        """Check every block against the content it claims to describe.

        Three failures, all invisible downstream. A block whose text is not what
        its span covers mis-slices every chunk built from it. Blocks out of
        reading order give chunk indices that scramble a multi-chunk answer while
        every individual citation still looks correct. Overlapping blocks
        duplicate text -- the service reports a table's cells as paragraphs as
        well as in the table itself -- so a retriever would see each row twice
        and a citation could land on either copy.
        """
        previous_start = -1
        previous_end = 0
        for block in self.blocks:
            if block.end > len(self.content):
                msg = (
                    f"block {block.start}:{block.end} reaches past content "
                    f"of {len(self.content)} characters"
                )
                raise ValueError(msg)
            if self.content[block.start : block.end] != block.text:
                msg = f"block at {block.start}:{block.end} does not match the content it spans"
                raise ValueError(msg)
            if block.start < previous_start:
                msg = f"block at {block.start} is not in reading order"
                raise ValueError(msg)
            if block.start < previous_end:
                msg = f"block at {block.start} overlaps the one ending at {previous_end}"
                raise ValueError(msg)
            previous_start = block.start
            previous_end = block.end
        return self

    def headings(self) -> tuple[ParsedBlock, ...]:
        """Every title and section heading, in reading order."""
        return tuple(block for block in self.blocks if block.role in HEADING_ROLES)

    def tables(self) -> tuple[ParsedBlock, ...]:
        """Every table, in reading order. ADR 0006 makes each one its own chunk."""
        return tuple(block for block in self.blocks if block.role is BlockRole.TABLE)


@runtime_checkable
class DocumentParser(Protocol):
    """Turns bytes into a :class:`ParsedDocument`.

    ``parse`` is async because the Azure implementation is a network call and the
    others read files; none may hold the event loop. A parser that cannot read
    what it was given raises :class:`ParseError` rather than returning an empty
    document -- an empty document is indistinguishable from a blank page, and
    this corpus contains neither.
    """

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument: ...
