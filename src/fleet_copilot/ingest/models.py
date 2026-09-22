"""Typed domain models for the ingest stage.

These two models are the contract between ingest and everything downstream:
retrieval indexes :class:`Chunk`, agents cite it, and evals score against it.
Both are frozen and reject unknown fields, so a chunk that reaches the
retriever is known to be well-formed and cannot drift underneath it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from fleet_copilot.corpus.models import DocumentType, Language

NonEmptyStr = Annotated[str, Field(min_length=1)]
"""A string that must carry at least one character after stripping."""


class Document(BaseModel):
    """A source document, before it has been split into chunks."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    doc_id: NonEmptyStr
    source_uri: NonEmptyStr
    text: NonEmptyStr
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("ingested_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        """Reject naive timestamps and normalise everything to UTC.

        Ingests run in more than one region; a naive timestamp cannot be
        ordered against one from another region without guessing its offset.
        """
        if value.tzinfo is None:
            msg = "ingested_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def checksum(self) -> str:
        """SHA-256 of the document text, used to skip unchanged re-ingests."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class StrategyId(StrEnum):
    """Which chunking strategy produced a chunk.

    Part of chunk_id rather than only of the surrounding run: all three
    strategies chunk the same documents and share one embedding cache, so two
    of their chunks would otherwise collide on a key and the last write wins.
    """

    FIXED = "fixed"
    STRUCTURAL = "structural"
    CONTEXTUAL = "contextual"


class Chunk(BaseModel):
    """A contiguous slice of a :class:`Document`; the unit the retriever indexes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: NonEmptyStr
    type: DocumentType
    machine_types: tuple[str, ...] = ()
    item_numbers: tuple[str, ...] = ()
    language: Language
    revision: Annotated[int, Field(ge=1)]
    effective_date: date

    section_path: tuple[str, ...] = ()
    """Headings above this chunk, outermost first, with their '#' markers removed.

    Empty for the fixed-size baseline, which does not read headings -- that
    difference is one of the things the comparison is measuring.
    """

    chunk_index: Annotated[int, Field(ge=0)]
    strategy: StrategyId

    text: NonEmptyStr
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    context_prefix: NonEmptyStr | None = None
    """The contextual header, outside the span on purpose.

    A chunk's subject often appears only in the heading above it, which is not
    in its own text; embedding the breadcrumb recovers that without moving
    start/end, so a citation still highlights the source exactly.
    """

    @field_validator("context_prefix")
    @classmethod
    def _reject_a_blank_prefix(cls, value: str | None) -> str | None:
        """Treat a whitespace-only prefix as the error it is, not as no prefix.

        A blank prefix produces embed_text with two leading newlines, embedding
        one chunk slightly differently from its unprefixed neighbours -- the kind
        of skew that makes a strategy A/B measure the wrong thing.
        """
        if value is not None and not value.strip():
            msg = "context_prefix must not be blank; omit it instead"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _check_span_matches_text(self) -> Self:
        """Keep the span honest so citations can point back into the source.

        A chunk whose offsets disagree with its own text will highlight the
        wrong passage in the UI, and the error is invisible until a human reads
        the citation -- so it is rejected at construction time.
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

    @model_validator(mode="after")
    def _prefix_belongs_to_the_contextual_strategy_alone(self) -> Self:
        """Tie the header to the strategy named after it.

        A contextual chunk without a prefix is byte-identical to its structural
        twin and would duplicate it in the index under a different id; a fixed
        chunk with one would quietly stop being a baseline.
        """
        wants_prefix = self.strategy is StrategyId.CONTEXTUAL
        if wants_prefix and self.context_prefix is None:
            msg = "the contextual strategy requires a context_prefix"
            raise ValueError(msg)
        if not wants_prefix and self.context_prefix is not None:
            msg = f"the {self.strategy.value} strategy must not carry a context_prefix"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def chunk_id(self) -> str:
        """Identifier that is stable across re-ingests of the same document."""
        return f"{self.doc_id}:{self.strategy.value}:{self.chunk_index}"

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def embed_text(self) -> str:
        """What the retriever embeds, as opposed to what a citation quotes."""
        if self.context_prefix is None:
            return self.text
        return f"{self.context_prefix}\n\n{self.text}"

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def content_hash(self) -> str:
        """SHA-256 of what is actually embedded. The embedding cache key.

        Deliberately over embed_text and not over text: see ADR 0006. Two
        strategies can produce the same slice with different headers, and
        hashing text would hand them one cached vector between them.
        """
        return hashlib.sha256(self.embed_text.encode("utf-8")).hexdigest()
