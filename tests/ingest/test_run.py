"""The cache wrapper, and the dispatch that decides which parser runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.ingest.cache import LocalLayoutCache, cache_key
from fleet_copilot.ingest.parse import ParsedDocument
from fleet_copilot.ingest.run import CachedParser, analyse_targets, native_targets

FIXTURE = Path(__file__).parent / "fixtures" / "layout" / "sdm-43-service-manual.json"

DATA = b"stands in for the pdf; the fake analyser ignores it"


class CountingAnalyser:
    """Stands in for AzureLayoutParser, and counts how often it was called."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def analyse(self, data: bytes) -> Mapping[str, Any]:
        self.calls += 1
        return self.payload


def a_payload() -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


@pytest.mark.asyncio
async def test_the_second_parse_of_the_same_bytes_does_not_call_the_service(
    tmp_path: Path,
) -> None:
    """The reason the cache exists.

    Three chunking strategies will read these documents. A comparison is only
    meaningful if every one reads identical input, which a re-analysed document
    does not guarantee.
    """
    analyser = CountingAnalyser(a_payload())
    parser = CachedParser(analyser, LocalLayoutCache(tmp_path), api_version="2024-11-30")

    first = await parser.parse(DATA, doc_id="sdm-43-service-manual", content_type="application/pdf")
    second = await parser.parse(
        DATA, doc_id="sdm-43-service-manual", content_type="application/pdf"
    )

    assert analyser.calls == 1
    assert isinstance(first, ParsedDocument)
    assert first.content == second.content
    assert first.blocks == second.blocks


@pytest.mark.asyncio
async def test_the_cached_entry_lands_under_the_content_hash(tmp_path: Path) -> None:
    parser = CachedParser(
        CountingAnalyser(a_payload()), LocalLayoutCache(tmp_path), api_version="2024-11-30"
    )

    await parser.parse(DATA, doc_id="sdm-43-service-manual", content_type="application/pdf")

    expected = cache_key(
        source_sha256=hashlib.sha256(DATA).hexdigest(),
        model_id="prebuilt-layout",
        api_version="2024-11-30",
    )
    assert (tmp_path / expected).is_file()


def test_only_the_converted_documents_are_sent_to_the_service() -> None:
    """Markdown is parsed natively; sending it would pay per page for nothing."""
    manifest = load_manifest(manifest_path(None))

    assert len(analyse_targets(manifest)) == 25
    assert len(native_targets(manifest)) == 95
    assert len(analyse_targets(manifest)) + len(native_targets(manifest)) == manifest.total
    assert corpus_root(None).is_dir()


def test_every_target_carries_the_content_type_its_parser_needs() -> None:
    manifest = load_manifest(manifest_path(None))

    assert {t.content_type for t in native_targets(manifest)} == {"text/markdown; charset=utf-8"}
    assert {t.content_type for t in analyse_targets(manifest)} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
