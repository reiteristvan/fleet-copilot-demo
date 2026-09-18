"""Parse the Markdown documents natively.

Ninety-five of the corpus's 120 documents are Markdown, and their structure is
already in the text: ATX headings mark the sections, pipe rows mark the tables.
Sending them to Document Intelligence would pay per page to recover what is
sitting in plain sight, and would OCR prose we wrote ourselves.

The output is the same ParsedDocument the Azure parser produces, so a chunker
cannot tell which path a document came down.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Final

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseError,
    ParserId,
)

MODEL_ID: Final = "markdown"
FRONT_MATTER_FENCE: Final = "---\n"


def strip_front_matter(text: str) -> str:
    """Return ``text`` without its YAML front matter block.

    The metadata is not prose. Left in, the fixed-size baseline would spend its
    first chunk on YAML, and the contextual-header strategy would embed the
    doc_id once in the header and once in the body.
    """
    if not text.startswith(FRONT_MATTER_FENCE):
        return text
    rest = text[len(FRONT_MATTER_FENCE) :]
    end = rest.find("\n" + FRONT_MATTER_FENCE)
    if end == -1:
        return text
    return rest[end + 1 + len(FRONT_MATTER_FENCE) :].lstrip("\n")


def _line_spans(content: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, text)`` per line, with the line ending excluded.

    Offsets accumulate as the lines are walked rather than being searched for:
    two identical bullets in one document -- which the fragment banks produce
    routinely -- would both find the first occurrence.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip("\r")
        spans.append((cursor, cursor + len(stripped), stripped))
        cursor += len(line)
    return spans


def _classify(line: str) -> BlockRole | None:
    """Return the role of ``line``, or None for a blank one."""
    if not line.strip():
        return None
    if line.startswith("#"):
        level = len(line) - len(line.lstrip("#"))
        return BlockRole.TITLE if level == 1 else BlockRole.SECTION_HEADING
    if line.lstrip().startswith("|"):
        return BlockRole.TABLE
    return BlockRole.PARAGRAPH


def _blocks(content: str) -> tuple[ParsedBlock, ...]:
    """Group lines into blocks: headings alone, everything else in runs."""
    blocks: list[ParsedBlock] = []
    run_role: BlockRole | None = None
    run_start = 0
    run_end = 0

    def close() -> None:
        nonlocal run_role
        if run_role is not None:
            blocks.append(
                ParsedBlock(
                    role=run_role,
                    text=content[run_start:run_end],
                    start=run_start,
                    end=run_end,
                    page_number=1,
                )
            )
            run_role = None

    for start, end, line in _line_spans(content):
        role = _classify(line)
        if role in (BlockRole.TITLE, BlockRole.SECTION_HEADING):
            close()
            blocks.append(
                ParsedBlock(role=role, text=content[start:end], start=start, end=end, page_number=1)
            )
            continue
        if role is not run_role:
            close()
        if role is not None:
            if run_role is None:
                run_role = role
                run_start = start
            run_end = end

    close()
    return tuple(blocks)


class MarkdownParser:
    """Parses Markdown natively. Implements :class:`DocumentParser`."""

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Parse ``data``.

        ``content_type`` is unused -- the caller dispatched on format to pick
        this parser -- but is in the signature because DocumentParser requires it.
        """
        text = await asyncio.to_thread(data.decode, "utf-8")
        content = strip_front_matter(text)
        if not content.strip():
            msg = f"{doc_id}: no content once the front matter is removed"
            raise ParseError(msg)

        return ParsedDocument(
            doc_id=doc_id,
            # The manifest hashes the file as written, front matter included.
            # Hashing the stripped body instead would make every cache lookup and
            # every drift check miss.
            source_sha256=hashlib.sha256(data).hexdigest(),
            parser=ParserId.MARKDOWN,
            model_id=MODEL_ID,
            api_version=None,
            content=content,
            blocks=_blocks(content),
            # Markdown has no pages. One page covering the whole document keeps
            # the shape identical to the Azure parser's rather than making every
            # consumer special-case an empty tuple.
            pages=(ParsedPage(page_number=1, start=0, end=len(content)),),
            parsed_at=datetime.now(UTC),
        )
