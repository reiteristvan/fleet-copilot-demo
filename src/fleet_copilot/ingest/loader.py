"""Read source documents without blocking the event loop."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fleet_copilot.ingest.models import Document


def _read_text(path: Path) -> tuple[Path, str]:
    """Resolve ``path`` and read it. Blocking; call via :func:`asyncio.to_thread`."""
    return path.resolve(), path.read_text(encoding="utf-8")


def _matching_paths(directory: Path, pattern: str) -> list[Path]:
    """List files in ``directory`` matching ``pattern``. Blocking; see above."""
    return sorted(path for path in directory.glob(pattern) if path.is_file())


async def load_document(path: Path, *, doc_id: str | None = None) -> Document:
    """Read ``path`` and wrap its contents in a :class:`Document`.

    Every filesystem touch is pushed onto a worker thread: ``pathlib`` is
    blocking throughout, and holding the event loop during ingest would
    stall every other request the API is serving.
    """
    resolved, text = await asyncio.to_thread(_read_text, path)
    return Document(
        doc_id=path.stem if doc_id is None else doc_id,
        source_uri=resolved.as_uri(),
        text=text,
    )


async def load_directory(directory: Path, *, pattern: str = "*.txt") -> list[Document]:
    """Load every file in ``directory`` matching ``pattern``, concurrently.

    Results are ordered by path so that a re-ingest of an unchanged
    directory produces the same chunk ordinals as the run before it.
    """
    paths = await asyncio.to_thread(_matching_paths, directory, pattern)
    documents = await asyncio.gather(*(load_document(path) for path in paths))
    return list(documents)
