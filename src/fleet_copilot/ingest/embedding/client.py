"""What turns text into vectors.

Two implementations behind one Protocol, the same shape as the parsers and the
caches: :class:`StubEmbedder` is deterministic, offline and counts its calls, so
the whole suite -- including the "zero calls on a re-run" criterion -- runs
without an Azure account; :class:`AzureEmbedder` is what a real run uses.

The SDK is imported inside the method that needs it, so constructing an
AzureEmbedder costs nothing until it is actually asked for vectors.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from fleet_copilot.config import Settings

Vector = tuple[float, ...]


@runtime_checkable
class Embedder(Protocol):
    """Embeds a batch of strings, in order."""

    @property
    def model_id(self) -> str: ...

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


class StubEmbedder:
    """Deterministic fake vectors derived from the text. Implements Embedder.

    Derived from a hash rather than random so the same text gives the same
    vector across processes: a cache test whose "hit" returned a different
    vector from its "miss" would pass for the wrong reason.
    """

    def __init__(self, dimensions: int = 8, model_id: str = "stub") -> None:
        self._dimensions = dimensions
        self._model_id = model_id
        self.calls = 0
        self.inputs = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        self.calls += 1
        self.inputs += len(texts)
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> Vector:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat the digest until it covers the requested width; four bytes per
        # float, unpacked as unsigned ints and scaled into [0, 1).
        needed = self._dimensions * 4
        raw = (digest * (needed // len(digest) + 1))[:needed]
        return tuple(value / 0xFFFFFFFF for value in struct.unpack(f">{self._dimensions}I", raw))


class AzureEmbedder:
    """Calls the deployed text-embedding-3-large. Implements Embedder.

    Authentication is an Entra token provider and nothing else: ADR 0002
    disables local auth on the account, so a key here would not fail in review,
    it would fail at runtime.

    Retry is the SDK's. It already backs off on 408, 409, 429 and 5xx and reads
    the Retry-After header Azure sends, which a hand-rolled exponential backoff
    ignores -- retrying early and making the throttle worse. What this class
    owns is `max_retries`; what the caller owns is the batch size, which is what
    decides whether a 429 happens at all (ADR 0007).
    """

    SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self, settings: Settings) -> None:
        if not settings.azure_openai_endpoint:
            msg = (
                "AZURE_OPENAI_ENDPOINT is not set. Populate it from "
                "`./infra/deploy.sh dev`, which prints it as an export line."
            )
            raise ValueError(msg)
        self._settings = settings

    @property
    def model_id(self) -> str:
        """The deployment, which is what the cache key records.

        Deliberately the deployment name and not the model name: two deployments
        of the same model can differ in version, and a cache that could not tell
        them apart would serve one's vectors for the other.
        """
        return self._settings.azure_openai_embedding_deployment

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed ``texts`` in one request, preserving order."""
        from azure.identity.aio import get_bearer_token_provider
        from openai import AsyncAzureOpenAI

        from fleet_copilot.credentials import get_async_credential

        settings = self._settings
        credential = get_async_credential()
        async with credential:
            client = AsyncAzureOpenAI(
                azure_endpoint=str(settings.azure_openai_endpoint),
                azure_deployment=settings.azure_openai_embedding_deployment,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=get_bearer_token_provider(credential, self.SCOPE),
                max_retries=settings.embedding_max_retries,
            )
            async with client:
                response = await client.embeddings.create(
                    input=list(texts),
                    model=settings.azure_openai_embedding_deployment,
                )

        # The API documents the order as matching the input, but it also returns
        # an explicit index; sorting on it costs nothing and removes a class of
        # bug where every vector is attributed to the wrong chunk and nothing
        # looks broken until a citation is read.
        ordered = sorted(response.data, key=lambda item: item.index)
        return tuple(tuple(item.embedding) for item in ordered)
