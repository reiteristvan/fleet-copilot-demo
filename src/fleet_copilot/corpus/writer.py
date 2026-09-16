"""Write the planned corpus to disk and build its manifest.

Layout under ``data/corpus/``::

    markdown/<doc_id>.md    every document, the reviewable source of truth
    dist/<doc_id>.<ext>     the converted subset, and what actually gets uploaded

Manifest paths are relative to the corpus root, so the manifest is readable from
a checkout, a temporary directory in a test, or anywhere else it is unpacked.

Every document is written as Markdown even when it is uploaded as something
else, because a diff of the Markdown is the only way to review what a change to
a fragment bank did to the corpus -- a PDF diff says nothing. The manifest
points at the file that gets uploaded, which for a converted document is the
one in ``dist/``: ADR 0003 has a converted document replace its Markdown rather
than accompany it, so that uploading both does not plant an exact-duplicate
pair nobody intended.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fleet_copilot.corpus import formats, render
from fleet_copilot.corpus.manifest import Manifest, ManifestEntry, sha256_of
from fleet_copilot.corpus.models import OutputFormat
from fleet_copilot.corpus.planner import PlannedDocument

MARKDOWN_DIR = "markdown"
DIST_DIR = "dist"

_EXTENSIONS = {
    OutputFormat.MARKDOWN: ".md",
    OutputFormat.PDF: ".pdf",
    OutputFormat.SCANNED_PDF: ".pdf",
    OutputFormat.DOCX: ".docx",
}


def _render_converted(document: PlannedDocument) -> bytes:
    """Render ``document`` in its non-Markdown output format."""
    match document.output_format:
        case OutputFormat.PDF:
            return formats.render_pdf(document.plan)
        case OutputFormat.SCANNED_PDF:
            return formats.render_scanned_pdf(document.plan)
        case OutputFormat.DOCX:
            return formats.render_docx(document.plan)
        case OutputFormat.MARKDOWN:
            msg = "Markdown is not a converted format"
            raise ValueError(msg)


def _write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_corpus(documents: Sequence[PlannedDocument], corpus_root: Path, *, seed: int) -> Manifest:
    """Write every document under ``corpus_root`` and return the manifest.

    Markdown is written as UTF-8 bytes with the line endings
    :mod:`fleet_copilot.corpus.render` chose, rather than through
    :meth:`Path.write_text`, which would translate them to CRLF on Windows and
    give a corpus generated there a different hash from the same corpus
    generated in CI.
    """
    entries: list[ManifestEntry] = []

    for document in documents:
        markdown = render.render_markdown(document.plan).encode("utf-8")
        markdown_path = corpus_root / MARKDOWN_DIR / f"{document.doc_id}.md"
        _write(markdown_path, markdown)

        if document.output_format is OutputFormat.MARKDOWN:
            published_path, published = markdown_path, markdown
        else:
            published = _render_converted(document)
            extension = _EXTENSIONS[document.output_format]
            published_path = corpus_root / DIST_DIR / f"{document.doc_id}{extension}"
            _write(published_path, published)

        planted = document.planted
        entries.append(
            ManifestEntry(
                doc_id=document.doc_id,
                type=document.plan.front_matter.type,
                language=document.language,
                format=document.output_format,
                path=published_path.relative_to(corpus_root).as_posix(),
                sha256=sha256_of(published),
                bytes=len(published),
                planted=planted is not None,
                planted_kind=planted.kind if planted else None,
                planted_id=planted.id if planted else None,
                planted_note=planted.note if planted else None,
            )
        )

    return Manifest(seed=seed, total=len(entries), documents=tuple(entries))
