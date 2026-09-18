"""Azure AI Document Intelligence, and the mapping from its output to ours.

Split the way corpus/upload.py is split: everything with a decision in it is a
pure function over the response payload, tested against committed fixtures, and
only :class:`AzureLayoutParser` touches the network.

The SDK is imported inside the method that needs it. Parsing is a dev-time
operation -- the API image installs with --no-dev -- so this module has to stay
importable without azure-ai-documentintelligence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseError,
    ParserId,
)

MODEL_ID: Final = "prebuilt-layout"
"""Layout, not Read. Read is OCR alone and would leave us reconstructing
structure by counting '#' characters -- of which the five scanned PDFs in this
corpus contain none."""

ROLE_BY_DI_NAME: Final[Mapping[str, BlockRole]] = {
    "title": BlockRole.TITLE,
    "sectionHeading": BlockRole.SECTION_HEADING,
    "pageHeader": BlockRole.PAGE_HEADER,
    "pageFooter": BlockRole.PAGE_FOOTER,
    "pageNumber": BlockRole.PAGE_NUMBER,
    "footnote": BlockRole.FOOTNOTE,
    "formulaBlock": BlockRole.FORMULA_BLOCK,
}
"""The service's seven paragraph roles. A paragraph carrying no role at all is
ordinary body text -- the common case -- and maps to PARAGRAPH."""


def _span_bounds(spans: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Return the outer bounds of ``spans``.

    A paragraph is usually one span. Where it is more -- one the service split
    across a column break -- taking the outer bounds keeps text equal to
    content[start:end], which concatenating the spans would not.
    """
    offsets = [int(span["offset"]) for span in spans]
    ends = [int(span["offset"]) + int(span["length"]) for span in spans]
    return min(offsets), max(ends)


def _page_number(element: Mapping[str, Any]) -> int:
    """Return the page an element sits on, defaulting to the first."""
    regions = element.get("boundingRegions") or [{"pageNumber": 1}]
    return int(regions[0]["pageNumber"])


def _table_blocks(
    payload: Mapping[str, Any], content: str
) -> tuple[tuple[ParsedBlock, ...], tuple[tuple[int, int], ...]]:
    """Return the table blocks, and the spans they occupy.

    The spans come back too so the paragraph pass can drop the cells the service
    reports twice -- once as paragraphs, once inside the table.
    """
    blocks: list[ParsedBlock] = []
    spans: list[tuple[int, int]] = []
    for table in payload.get("tables") or []:
        table_spans = table.get("spans") or []
        if not table_spans:
            continue
        start, end = _span_bounds(table_spans)
        text = content[start:end]
        if not text.strip():
            continue
        spans.append((start, end))
        blocks.append(
            ParsedBlock(
                role=BlockRole.TABLE,
                text=text,
                start=start,
                end=end,
                page_number=_page_number(table),
            )
        )
    return tuple(blocks), tuple(spans)


def _intersects(start: int, end: int, spans: Sequence[tuple[int, int]]) -> bool:
    """Whether ``start:end`` overlaps any of ``spans`` at all.

    Intersection rather than containment: the service sometimes reports a
    paragraph that straddles a table boundary, and keeping it would overlap the
    table block, which ParsedDocument rejects outright.
    """
    return any(start < span_end and span_start < end for span_start, span_end in spans)


def layout_from_analyze_result(
    payload: Mapping[str, Any], *, doc_id: str, source_sha256: str
) -> ParsedDocument:
    """Map one ``AnalyzeResult`` payload onto :class:`ParsedDocument`.

    ``payload`` is ``AnalyzeResult.as_dict()``, which is also exactly what the
    cache stores -- so this runs identically on a fresh response and a cached
    one, and a change here never costs an analyse call.
    """
    content = str(payload.get("content") or "")
    if not content:
        msg = (
            f"{doc_id}: the analyse call succeeded but returned no content. "
            "That is what the F0 tier does; check the account is S0."
        )
        raise ParseError(msg)

    pages: list[ParsedPage] = []
    for page in payload.get("pages") or []:
        page_spans = page.get("spans") or [{"offset": 0, "length": len(content)}]
        page_start, page_end = _span_bounds(page_spans)
        pages.append(
            ParsedPage(page_number=int(page["pageNumber"]), start=page_start, end=page_end)
        )

    table_blocks, table_spans = _table_blocks(payload, content)
    blocks: list[ParsedBlock] = list(table_blocks)

    for paragraph in payload.get("paragraphs") or []:
        spans = paragraph.get("spans") or []
        if not spans:
            continue
        start, end = _span_bounds(spans)
        # Cells arrive as paragraphs as well as inside the table. Keeping both
        # would put every row in the index twice and let a citation land on
        # either copy; ParsedDocument rejects the overlap outright.
        if _intersects(start, end, table_spans):
            continue
        text = content[start:end]
        if not text:
            continue
        blocks.append(
            ParsedBlock(
                role=ROLE_BY_DI_NAME.get(str(paragraph.get("role") or ""), BlockRole.PARAGRAPH),
                # Derived from the span, never taken from paragraph["content"]:
                # in Markdown mode the service returns the undecorated text
                # there, which does not always coincide with what the span
                # covers. Deriving makes the span invariant true by construction.
                text=text,
                start=start,
                end=end,
                page_number=_page_number(paragraph),
            )
        )

    blocks.sort(key=lambda block: block.start)

    return ParsedDocument(
        doc_id=doc_id,
        source_sha256=source_sha256,
        parser=ParserId.AZURE_LAYOUT,
        model_id=str(payload.get("modelId") or MODEL_ID),
        api_version=str(payload["apiVersion"]) if payload.get("apiVersion") else None,
        content=content,
        blocks=tuple(blocks),
        pages=tuple(pages),
        parsed_at=datetime.now(UTC),
    )
