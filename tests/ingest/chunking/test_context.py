"""Where a chunk's metadata and its breadcrumb come from."""

from __future__ import annotations

from datetime import date

from fleet_copilot.corpus.manifest import ManifestEntry
from fleet_copilot.ingest.chunking.context import (
    DocumentContext,
    contextual_header,
    heading_text,
    section_path_at,
)
from fleet_copilot.ingest.parse import BlockRole, ParsedBlock, ParsedDocument, ParsedPage, ParserId

CONTENT = (
    "# Single-disc machine SDM-43 - service manual\n"
    "\n"
    "## Scope\n"
    "\n"
    "For service technicians.\n"
    "\n"
    "## Safety\n"
    "\n"
    "The emergency stop is not an isolator.\n"
)


def an_entry() -> ManifestEntry:
    return ManifestEntry.model_validate(
        {
            "doc_id": "sdm-43-service-manual",
            "type": "service_manual",
            "language": "en",
            "format": "pdf",
            "path": "published/sdm-43-service-manual.pdf",
            "sha256": "0" * 64,
            "bytes": 2639,
            "machine_types": ["SDM-43"],
            "item_numbers": ["1.291-101.0"],
            "revision": 3,
            "effective_date": "2026-03-09",
        }
    )


def a_document() -> ParsedDocument:
    def block(role: BlockRole, needle: str) -> ParsedBlock:
        start = CONTENT.index(needle)
        return ParsedBlock(
            role=role, text=needle, start=start, end=start + len(needle), page_number=1
        )

    return ParsedDocument(
        doc_id="sdm-43-service-manual",
        source_sha256="0" * 64,
        parser=ParserId.MARKDOWN,
        model_id="markdown",
        content=CONTENT,
        blocks=(
            block(BlockRole.TITLE, "# Single-disc machine SDM-43 - service manual"),
            block(BlockRole.SECTION_HEADING, "## Scope"),
            block(BlockRole.PARAGRAPH, "For service technicians."),
            block(BlockRole.SECTION_HEADING, "## Safety"),
            block(BlockRole.PARAGRAPH, "The emergency stop is not an isolator."),
        ),
        pages=(ParsedPage(page_number=1, start=0, end=len(CONTENT)),),
    )


def test_heading_text_drops_the_markers() -> None:
    """Block text is a verbatim slice, so a heading arrives with its '#' intact.

    Left in, every breadcrumb would read '## Safety' and the hash markers would
    be embedded along with the words.
    """
    assert heading_text("## Safety") == "Safety"
    assert heading_text("# Single-disc machine SDM-43 - service manual") == (
        "Single-disc machine SDM-43 - service manual"
    )


def test_the_section_path_is_the_headings_above_an_offset() -> None:
    document = a_document()
    offset = CONTENT.index("The emergency stop")

    assert section_path_at(document, offset) == (
        "Single-disc machine SDM-43 - service manual",
        "Safety",
    )


def test_a_later_heading_replaces_an_earlier_one_at_the_same_level() -> None:
    """Scope and Safety are siblings; a chunk in Safety must not claim Scope."""
    document = a_document()

    assert section_path_at(document, CONTENT.index("For service")) == (
        "Single-disc machine SDM-43 - service manual",
        "Scope",
    )


def test_an_offset_before_any_heading_has_an_empty_path() -> None:
    assert section_path_at(a_document(), 0) == ()


def test_the_context_carries_every_metadata_field_a_chunk_needs() -> None:
    context = DocumentContext.from_entry(an_entry())

    fields = context.chunk_fields()
    assert fields["machine_types"] == ("SDM-43",)
    assert fields["item_numbers"] == ("1.291-101.0",)
    assert fields["revision"] == 3
    assert fields["effective_date"] == date(2026, 3, 9)


def test_the_contextual_header_names_the_machine_and_the_part() -> None:
    """ADR 0006 fixes the shape: title > section path | machines | item numbers.

    A query naming a machine type finds the chunk even when the prose does not
    repeat it, which is the whole reason strategy 3 exists.
    """
    context = DocumentContext.from_entry(an_entry())

    header = contextual_header(context, ("Single-disc machine SDM-43 - service manual", "Safety"))

    assert header == ("Single-disc machine SDM-43 - service manual > Safety | SDM-43 | 1.291-101.0")


def test_a_header_omits_empty_parts_rather_than_leaving_separators() -> None:
    """A handover note has no item numbers; a trailing ' | ' would be embedded."""
    entry = an_entry().model_copy(update={"item_numbers": ()})

    header = contextual_header(DocumentContext.from_entry(entry), ("Manual", "Safety"))

    assert header == "Manual > Safety | SDM-43"
