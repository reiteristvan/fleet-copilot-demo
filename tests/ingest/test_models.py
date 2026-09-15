"""Tests for the ingest domain models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Final

import pytest
from pydantic import ValidationError

from fleet_copilot.ingest.models import Chunk, Document

VALID_DOCUMENT: Final[dict[str, object]] = {
    "doc_id": "manual-a4",
    "source_uri": "file:///manuals/a4.txt",
    "text": "Check tyre pressure monthly.",
}


def _document_payload(**overrides: object) -> dict[str, object]:
    """A valid Document payload with ``overrides`` applied.

    Invalid payloads are fed through ``model_validate`` rather than the
    constructor, so the rejection is exercised at runtime without having to
    lie to the type checker about the input.
    """
    return {**VALID_DOCUMENT, **overrides}


def test_checksum_changes_with_text() -> None:
    original = Document.model_validate(_document_payload())
    edited = Document.model_validate(_document_payload(text="Check tyre pressure weekly."))

    assert original.checksum != edited.checksum
    assert original.checksum == Document.model_validate(_document_payload()).checksum


def test_naive_ingested_at_is_rejected() -> None:
    payload = _document_payload(ingested_at=datetime(2026, 1, 1, 12, 0))

    with pytest.raises(ValidationError, match="timezone-aware"):
        Document.model_validate(payload)


def test_aware_ingested_at_is_normalised_to_utc() -> None:
    budapest = timezone(timedelta(hours=2))
    payload = _document_payload(ingested_at=datetime(2026, 1, 1, 12, 0, tzinfo=budapest))

    document = Document.model_validate(payload)

    assert document.ingested_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert document.ingested_at.tzinfo is UTC


def test_unknown_fields_are_rejected() -> None:
    payload = _document_payload(autor="a typo for author")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Document.model_validate(payload)


def test_blank_text_is_rejected() -> None:
    payload = _document_payload(text="   ")

    with pytest.raises(ValidationError):
        Document.model_validate(payload)


def test_document_is_frozen() -> None:
    document = Document(
        doc_id="manual-a4",
        source_uri="file:///manuals/a4.txt",
        text="Check tyre pressure monthly.",
    )

    with pytest.raises(ValidationError, match="frozen"):
        # The pydantic mypy plugin already rejects this statically; the ignore
        # is what lets the test also prove the runtime guard is in place.
        document.doc_id = "something-else"  # type: ignore[misc]


def test_chunk_id_is_stable() -> None:
    chunk = Chunk(doc_id="manual-a4", ordinal=3, text="abcde", start=10, end=15)

    assert chunk.chunk_id == "manual-a4:3"


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        (10, 10, "must be greater than"),
        (10, 9, "must be greater than"),
        (10, 20, "covers 10 characters"),
        (10, 13, "covers 3 characters"),
    ],
)
def test_chunk_rejects_span_that_disagrees_with_text(start: int, end: int, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        Chunk(doc_id="manual-a4", ordinal=0, text="abcde", start=start, end=end)
