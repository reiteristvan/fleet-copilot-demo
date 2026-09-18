"""Where analysed layouts are kept, so they are analysed once.

The cache stores the raw ``AnalyzeResult`` payload, not our model of it.
Re-interpreting a layout is then free and re-analysing is the only thing that
costs -- the inverse of what caching the parsed model would give.

Two implementations behind one Protocol, the same shape as the parsers:
:class:`LocalLayoutCache` is a directory and is what the tests use, so they need
no Azure account; :class:`BlobLayoutCache` is what a real parse run uses.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

SHA256 = re.compile(r"^[0-9a-f]{64}$")

JSON_INDENT: Final = 2
"""Stored pretty-printed. These files are read by a human exactly once -- when a
mapping is behaving oddly -- and that one read is worth the bytes."""


def cache_key(*, source_sha256: str, model_id: str, api_version: str) -> str:
    """Return the cache path for one analysed document.

    Model id and API version are in the key rather than only in the payload so
    that changing either invalidates by construction. A cache keyed on content
    alone would serve prebuilt-read output to a caller that had moved to
    prebuilt-layout, and the difference -- no headings -- is indistinguishable
    from a document that genuinely has none.
    """
    if not SHA256.match(source_sha256):
        msg = f"source_sha256 must be a 64-character hex sha256, got {source_sha256!r}"
        raise ValueError(msg)
    return f"{model_id}/{api_version}/{source_sha256}.json"


@runtime_checkable
class LayoutCache(Protocol):
    """Stores and retrieves raw analyse payloads by key."""

    async def get(self, key: str) -> Mapping[str, Any] | None: ...

    async def put(self, key: str, payload: Mapping[str, Any]) -> None: ...


def _serialise(payload: Mapping[str, Any]) -> str:
    """Render ``payload`` deterministically, ending in a single newline."""
    return json.dumps(payload, indent=JSON_INDENT, sort_keys=True) + "\n"


def _read(path: Path) -> str | None:
    """Read ``path``, or None if it is not there. Blocking; use to_thread."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, creating parents. Blocking; use to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LocalLayoutCache:
    """A directory. Implements :class:`LayoutCache`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def get(self, key: str) -> Mapping[str, Any] | None:
        text = await asyncio.to_thread(_read, self._root / key)
        if text is None:
            return None
        payload: Mapping[str, Any] = json.loads(text)
        return payload

    async def put(self, key: str, payload: Mapping[str, Any]) -> None:
        await asyncio.to_thread(_write, self._root / key, _serialise(payload))


class BlobLayoutCache:
    """A blob container. Implements :class:`LayoutCache`.

    The SDK is imported per call rather than at module scope so this module stays
    importable without azure-storage-blob, which is a dev-only dependency.
    Authentication is get_credential() and nothing else: ADR 0002 disables
    shared-key access at the resource level, so a connection string here would
    not fail in review, it would fail at runtime.
    """

    def __init__(self, endpoint: str, container: str) -> None:
        self._endpoint = endpoint
        self._container = container

    def _download(self, key: str) -> bytes | None:
        """Blocking; called through to_thread."""
        from azure.core.exceptions import ResourceNotFoundError
        from azure.storage.blob import BlobServiceClient

        from fleet_copilot.credentials import get_credential

        client = BlobServiceClient(account_url=self._endpoint, credential=get_credential())
        blob = client.get_container_client(self._container).get_blob_client(key)
        try:
            data: bytes = blob.download_blob().readall()
        except ResourceNotFoundError:
            return None
        return data

    def _upload(self, key: str, data: bytes) -> None:
        """Blocking; called through to_thread."""
        from azure.storage.blob import BlobServiceClient, ContentSettings

        from fleet_copilot.credentials import get_credential

        client = BlobServiceClient(account_url=self._endpoint, credential=get_credential())
        blob = client.get_container_client(self._container).get_blob_client(key)
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

    async def get(self, key: str) -> Mapping[str, Any] | None:
        data = await asyncio.to_thread(self._download, key)
        if data is None:
            return None
        payload: Mapping[str, Any] = json.loads(data)
        return payload

    async def put(self, key: str, payload: Mapping[str, Any]) -> None:
        await asyncio.to_thread(self._upload, key, _serialise(payload).encode("utf-8"))
