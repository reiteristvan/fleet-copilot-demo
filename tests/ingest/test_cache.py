"""The cache that makes a chunking comparison reproducible."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.ingest.cache import LayoutCache, LocalLayoutCache, cache_key

PAYLOAD: dict[str, Any] = {
    "apiVersion": "2024-11-30",
    "modelId": "prebuilt-layout",
    "content": "x",
}


def test_the_key_carries_the_model_and_api_version() -> None:
    """Switching model or API version must invalidate, not silently reuse.

    A cache keyed on content alone would serve prebuilt-read output to a caller
    that had moved to prebuilt-layout, and the difference -- missing headings --
    looks exactly like a document that genuinely has none.
    """
    key = cache_key(source_sha256="a" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    assert key == f"prebuilt-layout/2024-11-30/{'a' * 64}.json"


def test_a_short_hash_is_refused() -> None:
    with pytest.raises(ValueError, match="sha256"):
        cache_key(source_sha256="abc", model_id="prebuilt-layout", api_version="2024-11-30")


@pytest.mark.asyncio
async def test_a_miss_returns_none(tmp_path: Path) -> None:
    assert await LocalLayoutCache(tmp_path).get("prebuilt-layout/2024-11-30/deadbeef.json") is None


@pytest.mark.asyncio
async def test_what_goes_in_comes_back_out(tmp_path: Path) -> None:
    cache = LocalLayoutCache(tmp_path)
    key = cache_key(source_sha256="b" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    await cache.put(key, PAYLOAD)

    assert await cache.get(key) == PAYLOAD


@pytest.mark.asyncio
async def test_it_stores_the_raw_payload_verbatim(tmp_path: Path) -> None:
    """The cache holds AnalyzeResult, not our model of it.

    Re-interpreting a layout -- adding a role, changing how tables serialise --
    must cost nothing, while re-analysing costs money and a round trip. A cache
    of ParsedDocument would invert that.
    """
    cache = LocalLayoutCache(tmp_path)
    key = cache_key(source_sha256="c" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    await cache.put(key, PAYLOAD)

    assert json.loads((tmp_path / key).read_text(encoding="utf-8")) == PAYLOAD


def test_local_cache_satisfies_the_protocol() -> None:
    assert isinstance(LocalLayoutCache(Path(".")), LayoutCache)
