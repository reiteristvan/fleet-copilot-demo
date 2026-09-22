"""The metadata a chunk carries, and the breadcrumb strategy 3 prepends.

Metadata comes from the manifest rather than from the parsed document: the PDF
and DOCX renderers drop the front matter, so for the 25 converted documents the
manifest is the only place machine types, revision and effective date survive
(ADR 0006).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from fleet_copilot.corpus.manifest import ManifestEntry
from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.ingest.parse import HEADING_ROLES, BlockRole, ParsedDocument

PATH_SEPARATOR: Final = " > "
FIELD_SEPARATOR: Final = " | "


def heading_text(text: str) -> str:
    """Strip the ATX markers from a heading block.

    Block text is a verbatim slice of content, so a heading arrives as '##
    Safety'. Left alone, every breadcrumb would carry hash marks into the
    embedding along with the words.
    """
    return text.lstrip("#").strip()


def _heading_level(text: str) -> int:
    """The ATX level of a heading block, or 1 for a title without markers."""
    stripped = len(text) - len(text.lstrip("#"))
    return max(1, stripped)


def section_path_at(document: ParsedDocument, offset: int) -> tuple[str, ...]:
    """The headings in force at ``offset``, outermost first.

    Levels replace rather than accumulate: two sibling sections at the same
    depth are alternatives, and a chunk inside the second one that still claimed
    the first would be retrieved for queries about a section it is not in.

    A heading starting exactly at ``offset`` counts. The structural chunker
    flushes on a heading, so every chunk it emits begins at one; excluding it
    would hand each chunk the breadcrumb of the section above the one it is
    actually in, and the text would contradict its own metadata.
    """
    path: dict[int, str] = {}
    for block in document.blocks:
        if block.start > offset:
            break
        if block.role not in HEADING_ROLES:
            continue
        level = 1 if block.role is BlockRole.TITLE else _heading_level(block.text)
        path[level] = heading_text(block.text)
        for deeper in [key for key in path if key > level]:
            del path[deeper]
    return tuple(path[level] for level in sorted(path))


class DocumentContext(BaseModel):
    """Everything a chunk inherits from its document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    type: DocumentType
    language: Language
    machine_types: tuple[str, ...]
    item_numbers: tuple[str, ...]
    revision: int
    effective_date: date

    @classmethod
    def from_entry(cls, entry: ManifestEntry) -> DocumentContext:
        """Build the context from a manifest entry."""
        return cls(
            doc_id=entry.doc_id,
            type=entry.type,
            language=entry.language,
            machine_types=entry.machine_types,
            item_numbers=entry.item_numbers,
            revision=entry.revision,
            effective_date=entry.effective_date,
        )

    def chunk_fields(self) -> dict[str, Any]:
        """The metadata keys every Chunk carries, ready to splat into one."""
        return {
            "doc_id": self.doc_id,
            "type": self.type,
            "language": self.language,
            "machine_types": self.machine_types,
            "item_numbers": self.item_numbers,
            "revision": self.revision,
            "effective_date": self.effective_date,
        }


def contextual_header(context: DocumentContext, section_path: tuple[str, ...]) -> str:
    """Build the header strategy 3 prepends before embedding.

    ``<section path> | <machine types> | <item numbers>``, with empty parts
    dropped rather than left as dangling separators -- a handover note has no
    item numbers, and a trailing ' | ' would be embedded along with everything
    else.
    """
    parts = [
        PATH_SEPARATOR.join(section_path),
        ", ".join(context.machine_types),
        ", ".join(context.item_numbers),
    ]
    return FIELD_SEPARATOR.join(part for part in parts if part)
