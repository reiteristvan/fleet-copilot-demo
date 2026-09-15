"""Tests for the async document loader."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from fleet_copilot.ingest.loader import load_directory, load_document


@pytest.mark.asyncio
async def test_load_document_derives_id_and_uri_from_path(tmp_path: Path) -> None:
    source = tmp_path / "manual-a4.txt"
    source.write_text("Check tyre pressure monthly.\n", encoding="utf-8")

    document = await load_document(source)

    assert document.doc_id == "manual-a4"
    assert document.text == "Check tyre pressure monthly."
    assert document.source_uri == source.resolve().as_uri()
    assert document.ingested_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_load_document_accepts_an_explicit_id(tmp_path: Path) -> None:
    source = tmp_path / "manual-a4.txt"
    source.write_text("Check tyre pressure monthly.", encoding="utf-8")

    document = await load_document(source, doc_id="fleet/manuals/a4")

    assert document.doc_id == "fleet/manuals/a4"


@pytest.mark.asyncio
async def test_load_directory_returns_matching_files_in_path_order(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignored", encoding="utf-8")

    documents = await load_directory(tmp_path)

    assert [document.doc_id for document in documents] == ["a", "b"]
    assert [document.text for document in documents] == ["first", "second"]
