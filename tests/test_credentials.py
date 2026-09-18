"""Tests for the shared Entra ID credential."""

from __future__ import annotations

import pytest
from azure.identity import DefaultAzureCredential

from fleet_copilot.credentials import get_credential


def test_returns_a_default_azure_credential() -> None:
    assert isinstance(get_credential(), DefaultAzureCredential)


@pytest.mark.parametrize("client_id", ["3f2b0c1e-0000-4000-8000-000000000000", None])
def test_same_call_shape_with_and_without_a_managed_identity(
    monkeypatch: pytest.MonkeyPatch, client_id: str | None
) -> None:
    """The Container Apps and laptop paths must not diverge.

    Container Apps sets AZURE_CLIENT_ID; a laptop does not. Both have to reach
    the same constructor, because a branch here is a branch that only one of
    the two environments ever exercises.
    """
    if client_id is None:
        monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    else:
        monkeypatch.setenv("AZURE_CLIENT_ID", client_id)

    assert isinstance(get_credential(), DefaultAzureCredential)


def test_async_credential_is_the_async_default_chain() -> None:
    """The aio SDK clients do not reject a synchronous credential at construction.

    They accept it and fail on the first request, inside a poller, where the
    traceback points at the SDK rather than at the credential that was wrong.
    """
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

    from fleet_copilot.credentials import get_async_credential

    assert isinstance(get_async_credential(), AsyncDefaultAzureCredential)
