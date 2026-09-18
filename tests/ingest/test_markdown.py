"""The native Markdown parser: 95 of the 120 documents take this path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fleet_copilot.ingest.markdown import MarkdownParser, strip_front_matter
from fleet_copilot.ingest.parse import BlockRole, DocumentParser, ParseError, ParserId

CORPUS = Path(__file__).resolve().parents[2] / "data" / "corpus" / "markdown"
MANUAL = CORPUS / "sdm-43-service-manual.md"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"


def test_front_matter_is_removed_from_the_content() -> None:
    """Front matter is metadata, not prose.

    Left in, the fixed-size baseline would spend its first chunk on YAML and the
    contextual-header strategy would embed the doc_id twice.
    """
    body = strip_front_matter(MANUAL.read_text(encoding="utf-8"))

    assert body.startswith("# Single-disc machine SDM-43")
    assert "doc_id:" not in body


def test_a_document_without_front_matter_is_left_alone() -> None:
    assert strip_front_matter("# Title\n\nBody.\n") == "# Title\n\nBody.\n"


@pytest.mark.asyncio
async def test_every_block_is_a_verbatim_slice_of_content() -> None:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.asyncio
async def test_atx_levels_become_title_and_section_heading() -> None:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    roles = [block.role for block in document.headings()]
    assert roles[0] is BlockRole.TITLE
    assert roles.count(BlockRole.TITLE) == 1, "a document has one title and many sections"
    assert BlockRole.SECTION_HEADING in roles
    assert any("Safety" in block.text for block in document.headings())


@pytest.mark.asyncio
async def test_a_markdown_table_becomes_one_table_block() -> None:
    """The service-interval table, pipes and all, as a single chunkable unit."""
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    tables = document.tables()
    assert len(tables) == 1
    assert tables[0].text.count("\n") >= 5, "header, separator and five interval rows"
    assert "1000 h" in tables[0].text


@pytest.mark.asyncio
async def test_the_source_hash_covers_the_original_bytes() -> None:
    """The manifest hashes the file, front matter included. Hashing the stripped
    body instead would make every cache lookup and drift check miss."""
    data = MANUAL.read_bytes()

    document = await MarkdownParser().parse(
        data, doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    assert document.source_sha256 == hashlib.sha256(data).hexdigest()
    assert document.parser is ParserId.MARKDOWN


@pytest.mark.asyncio
async def test_the_whole_markdown_corpus_parses() -> None:
    """Every Markdown document, because the failure mode here is one odd file.

    ParsedDocument validates spans, ordering and overlap on construction, so this
    exercises all three against 120 real documents in two languages.
    """
    parser = MarkdownParser()
    paths = sorted(CORPUS.glob("*.md"))
    assert len(paths) == 120

    for path in paths:
        document = await parser.parse(
            path.read_bytes(), doc_id=path.stem, content_type=MARKDOWN_TYPE
        )
        assert document.blocks, f"{path.name} produced no blocks"


@pytest.mark.asyncio
async def test_a_document_that_is_only_front_matter_raises() -> None:
    with pytest.raises(ParseError, match="no content"):
        await MarkdownParser().parse(
            b"---\ndoc_id: x\n---\n", doc_id="x", content_type=MARKDOWN_TYPE
        )


def test_markdown_parser_satisfies_the_protocol() -> None:
    assert isinstance(MarkdownParser(), DocumentParser)
