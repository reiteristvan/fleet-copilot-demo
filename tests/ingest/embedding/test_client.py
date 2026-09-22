"""The embedder interface, and the stub the rest of the suite runs against."""

from __future__ import annotations

import inspect

import pytest

from fleet_copilot.ingest.embedding import client as client_module
from fleet_copilot.ingest.embedding.client import Embedder, StubEmbedder


@pytest.mark.asyncio
async def test_the_stub_returns_one_vector_per_input() -> None:
    stub = StubEmbedder(dimensions=8)

    vectors = await stub.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert all(len(vector) == 8 for vector in vectors)


@pytest.mark.asyncio
async def test_the_stub_is_deterministic_for_the_same_text() -> None:
    """The cache is only testable if the same text gives the same vector."""
    stub = StubEmbedder(dimensions=8)

    first = await stub.embed(["alpha"])
    second = await stub.embed(["alpha"])

    assert first == second


@pytest.mark.asyncio
async def test_different_text_gives_a_different_vector() -> None:
    """A stub that returned one constant would let a cache test pass even if
    every chunk had been embedded to the same point."""
    stub = StubEmbedder(dimensions=8)

    vectors = await stub.embed(["alpha", "beta"])

    assert vectors[0] != vectors[1]


@pytest.mark.asyncio
async def test_the_stub_counts_calls_and_inputs() -> None:
    """The acceptance criterion is 'zero embedding calls', so something has to
    be able to count them without a bill."""
    stub = StubEmbedder(dimensions=8)

    await stub.embed(["a", "b"])
    await stub.embed(["c"])

    assert stub.calls == 2
    assert stub.inputs == 3


def test_the_stub_satisfies_the_protocol() -> None:
    assert isinstance(StubEmbedder(), Embedder)


def test_the_azure_embedder_never_takes_a_key() -> None:
    """ADR 0002. The account has disableLocalAuth, so a key fails at runtime
    rather than in review -- this moves the failure to import time."""
    source = inspect.getsource(client_module)

    assert "api_key" not in source
    assert "AzureKeyCredential" not in source
