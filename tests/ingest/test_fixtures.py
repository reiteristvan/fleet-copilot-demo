"""Assert the committed fixtures are what the rest of the ingest tests assume.

These three files stand in for the Document Intelligence service everywhere else
in the suite. If one is truncated, re-captured against a different API version,
or committed empty, every test reading it would go on passing against a weaker
document than it was written for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "layout"

SERVICE_MANUAL = "sdm-43-service-manual"
SCANNED_REPORT = "service-report-sd50b-2026-10142-17"
HUNGARIAN_DOCX = "sdr-90-operator-manual-hu"

STEMS = (SERVICE_MANUAL, SCANNED_REPORT, HUNGARIAN_DOCX)


def fixture(stem: str) -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return payload


def _roles(payload: Mapping[str, Any]) -> list[str]:
    return [str(p.get("role") or "paragraph") for p in payload.get("paragraphs") or []]


@pytest.mark.parametrize("stem", STEMS)
def test_fixture_is_a_usable_analyze_result(stem: str) -> None:
    payload = fixture(stem)

    assert payload["apiVersion"] == "2024-11-30"
    assert payload["modelId"] == "prebuilt-layout"
    assert payload["content"], "empty content means the analyse call returned nothing"
    assert payload["paragraphs"], "no paragraphs means no structure to chunk on"


@pytest.mark.parametrize("stem", STEMS)
def test_spans_are_code_point_offsets(stem: str) -> None:
    """The SDK defaults string_index_type to textElements, which counts grapheme
    clusters; Python indexes strings by code point. A fixture captured under the
    default would put every offset after the first combining sequence out by one
    or more, and nothing would say so."""
    assert fixture(stem)["stringIndexType"] == "unicodeCodePoint"


@pytest.mark.parametrize("stem", STEMS)
def test_every_fixture_carries_headings(stem: str) -> None:
    """Headings are what layout is bought for over read, on every format."""
    roles = _roles(fixture(stem))

    assert "sectionHeading" in roles, f"{stem} yielded no section headings"


def test_the_service_manual_is_not_truncated() -> None:
    """A regression pin on the corpus renderer, not on the service.

    fpdf2 leaves multi_cell's cursor at the right margin by default, and the
    corpus generator relied on it returning to the left. Every other line
    rendered off the page and this document reached the service as 699
    characters of clipped fragments -- while staying a valid, byte-reproducible
    PDF, so nothing in the corpus suite noticed. Re-capturing after that fix
    gave 2139. If this drops back, the renderer has regressed, not the parser.
    """
    payload = fixture(SERVICE_MANUAL)

    assert len(payload["content"]) > 2000
    assert "1000 h" in payload["content"], "the last service-interval row"
    assert "Support a raised deck mechanically" in payload["content"], "the closing bullet"


def test_only_the_docx_fixture_yields_a_table() -> None:
    """Tables survive the DOCX path and not the PDF one, and that is the corpus.

    `corpus/formats.py` renders a table into a PDF as space-separated text
    rather than a grid, so there is no ruling or alignment for the service to
    recognise. python-docx writes a real table, and the service finds it.

    ADR 0006 makes each table its own chunk, so the layout-aware chunker is
    exercised through this fixture. Asserting it here keeps that dependency
    visible rather than leaving a chunker test to fail confusingly later.
    """
    assert fixture(HUNGARIAN_DOCX).get("tables"), "the DOCX table did not survive"
    assert not fixture(SERVICE_MANUAL).get("tables")
    assert not fixture(SCANNED_REPORT).get("tables")


def test_the_scanned_fixture_proves_ocr_ran() -> None:
    """The scanned PDF has no text layer, so any content at all came from OCR.

    This is the fixture that makes the Azure path worth paying for. If it comes
    back empty, the local fallback and the service are indistinguishable.
    """
    payload = fixture(SCANNED_REPORT)

    assert len(payload["content"]) > 200
