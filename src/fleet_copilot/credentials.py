"""Entra ID authentication for every Azure data plane this project touches."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential


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
