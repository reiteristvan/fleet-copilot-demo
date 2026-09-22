"""Tests for the ingest domain models."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final

import pytest
from pydantic import ValidationError

from fleet_copilot.ingest.models import Chunk, Document, StrategyId

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


def a_chunk(**overrides: object) -> Chunk:
    """Build a valid Chunk, with fields replaced for the case at hand."""
    fields: dict[str, object] = {
        "doc_id": "sdm-43-service-manual",
        "type": "service_manual",
        "machine_types": ("SDM-43",),
        "item_numbers": ("1.291-101.0",),
        "language": "en",
        "revision": 3,
        "effective_date": date(2026, 3, 9),
        "section_path": ("Single-disc machine SDM-43 - service manual", "Safety"),
        "chunk_index": 14,
        "strategy": StrategyId.STRUCTURAL,
        "text": "The emergency stop is not an isolator.",
        "start": 1794,
        "end": 1832,
    }
    fields.update(overrides)
    return Chunk.model_validate(fields)


def test_embed_text_is_the_text_when_there_is_no_prefix() -> None:
    assert a_chunk().embed_text == "The emergency stop is not an isolator."


def test_embed_text_joins_the_prefix_without_disturbing_the_span() -> None:
    chunk = a_chunk(
        strategy=StrategyId.CONTEXTUAL,
        context_prefix=(
            "Single-disc machine SDM-43 - service manual > Safety | SDM-43 | 1.291-101.0"
        ),
    )

    assert chunk.embed_text.endswith("\n\nThe emergency stop is not an isolator.")
    # The span still describes text alone. That is the whole point of the field.
    assert chunk.end - chunk.start == len(chunk.text)


def test_content_hash_covers_embed_text_not_text() -> None:
    """ADR 0006's sharpest decision, asserted.

    Strategies 2 and 3 produce the same slice of the same document with a
    different header. Hashing text would give them one embedding cache key
    between them, and strategy 3 would be scored on strategy 2's vectors with
    every resulting number looking entirely plausible.
    """
    structural = a_chunk()
    contextual = a_chunk(strategy=StrategyId.CONTEXTUAL, context_prefix="Manual > Safety")

    assert structural.text == contextual.text
    assert structural.content_hash != contextual.content_hash
    assert (
        contextual.content_hash == hashlib.sha256(contextual.embed_text.encode("utf-8")).hexdigest()
    )


def test_chunk_id_distinguishes_the_strategies() -> None:
    """Three strategies chunk the same document and share an embedding cache.

    Without the strategy in the id, two of their chunks collide on the same key
    and whichever was written last wins.
    """
    assert a_chunk().chunk_id == "sdm-43-service-manual:structural:14"
    assert a_chunk(strategy=StrategyId.FIXED).chunk_id == "sdm-43-service-manual:fixed:14"


def test_a_blank_prefix_is_rejected() -> None:
    """A blank prefix is not the same as no prefix, and reads as one.

    It produces embed_text with two leading newlines, embedding one chunk
    slightly differently from its unprefixed neighbours -- exactly the silent
    skew an A/B between strategies cannot survive.
    """
    with pytest.raises(ValidationError):
        a_chunk(context_prefix="  ")


def test_a_contextual_chunk_must_carry_a_prefix() -> None:
    """The contextual strategy is defined by the header. One without it is
    indistinguishable from a structural chunk and would silently duplicate it."""
    with pytest.raises(ValidationError, match="context_prefix"):
        a_chunk(strategy=StrategyId.CONTEXTUAL)


def test_a_non_contextual_chunk_must_not_carry_a_prefix() -> None:
    with pytest.raises(ValidationError, match="context_prefix"):
        a_chunk(strategy=StrategyId.FIXED, context_prefix="Manual > Safety")


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
        a_chunk(text="abcde", start=start, end=end)
