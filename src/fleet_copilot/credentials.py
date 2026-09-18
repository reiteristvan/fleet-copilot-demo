"""Entra ID authentication for every Azure data plane this project touches."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential


def get_credential() -> DefaultAzureCredential:
    """Return the credential used both locally and in Container Apps.

    There is deliberately no branching on environment. DefaultAzureCredential
    already reads ``AZURE_CLIENT_ID`` for its managed-identity leg, which
    Container Apps sets to the user-assigned identity and a developer machine
    leaves unset; locally the chain falls through to the ``az login`` session
    instead. The code path exercised on a laptop is therefore the one that runs
    in production, which is the whole point of not writing a factory here.

    Callers should hold on to the returned instance: it caches tokens, and a
    fresh one re-authenticates.
    """
    return DefaultAzureCredential()


def get_async_credential() -> AsyncDefaultAzureCredential:
    """Return the same chain as :func:`get_credential`, for the aio clients.

    A second function rather than a branch: the two are different types with
    different close semantics, and an aio client handed the synchronous one
    accepts it and fails on the first request instead of at construction. The
    caller closes this one -- ``async with credential:`` -- because its
    transport holds a connection pool.
    """
    return AsyncDefaultAzureCredential()
