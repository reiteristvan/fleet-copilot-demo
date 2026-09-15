"""Typed domain models for the ingest stage.

These two models are the contract between ingest and everything downstream:
retrieval indexes :class:`Chunk`, agents cite it, and evals score against it.
Both are frozen and reject unknown fields, so a chunk that reaches the
retriever is known to be well-formed and cannot drift underneath it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

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


class Chunk(BaseModel):
    """A contiguous slice of a :class:`Document`; the unit the retriever indexes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: NonEmptyStr
    ordinal: Annotated[int, Field(ge=0)]
    text: NonEmptyStr
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _check_span_matches_text(self) -> Self:
        """Keep the span honest so citations can point back into the source.

        A chunk whose offsets disagree with its own text will highlight the
        wrong passage in the UI, and the error is invisible until a human
        reads the citation -- so it is rejected at construction time.
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

    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def chunk_id(self) -> str:
        """Identifier that is stable across re-ingests of the same document."""
        return f"{self.doc_id}:{self.ordinal}"
