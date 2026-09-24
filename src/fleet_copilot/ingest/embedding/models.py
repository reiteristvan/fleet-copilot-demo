"""What a cached embedding is, and what a run of them did."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbeddingRecord(BaseModel):
    """One vector, ready to be written to the cache."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    model_id: Annotated[str, Field(min_length=1)]
    dimensions: Annotated[int, Field(gt=0)]
    embedding: tuple[float, ...]

    @model_validator(mode="after")
    def _length_matches_dimensions(self) -> Self:
        """Catch a short vector here rather than in the database.

        Postgres rejects a short vector with a message about the column's declared
        width. That message is three layers from the code that built the row, and
        it does not name the chunk.
        """
        if len(self.embedding) != self.dimensions:
            msg = (
                f"embedding has {len(self.embedding)} values but dimensions says {self.dimensions}"
            )
            raise ValueError(msg)
        return self

    def literal(self) -> str:
        """Render the vector as the text form pgvector parses."""
        return "[" + ",".join(repr(value) for value in self.embedding) + "]"


class EmbedReport(BaseModel):
    """What an embedding run did, or would have done."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: int
    unique: int
    cached: int
    embedded: int
    requests: int
    dry_run: bool

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        verb = "would embed" if self.dry_run else "embedded"
        return (
            f"{self.chunks} chunks, {self.unique} distinct, {self.cached} already cached, "
            f"{verb} {self.embedded} in {self.requests} requests"
        )
