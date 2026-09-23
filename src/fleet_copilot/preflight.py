"""Check that this machine can actually reach every Azure data plane it needs.

Three times now a task has died partway through on a 401 or 403 whose message
named a data action, or nothing at all, but never a role: Document Intelligence
in story 1.2, blob upload in the same story, Azure OpenAI in the embedding pass.
Each time the fix was one role assignment nobody had made, and each time it was
found by spending a run to discover it.

**Every check makes a real data-plane call.** Reading role assignments instead
would be cheaper and would lie: when the OpenAI role was finally assigned, the
call kept failing for several minutes while the assignment propagated, and a
listing would have reported success throughout. What matters is whether the
request works now, so that is what is asked.

The calls are chosen to be free and read-only. The one exception is the
embedding probe, which sends a single word -- there is no read-only operation on
that data plane, and a token costs about a hundred-thousandth of a cent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from fleet_copilot.config import Settings

SKIPPED: Final = "skipped"
OK: Final = "ok"
FAILED: Final = "failed"


class CheckResult(BaseModel):
    """What one data-plane probe found."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: str
    role: str
    """The role that grants this call, named so a failure is actionable.

    The point of the whole module: Azure's own message does not include it.
    """

    detail: str = ""

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        mark = {OK: "ok  ", FAILED: "FAIL", SKIPPED: "--  "}[self.status]
        suffix = f"  ({self.detail})" if self.detail else ""
        if self.status == FAILED:
            suffix = f"  needs {self.role}{suffix}"
        return f"{mark} {self.name}{suffix}"


class Check(BaseModel):
    """A probe, and the role a human should be granted when it fails."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    role: str
    scope_setting: str
    """Which Settings field must be populated for this check to mean anything."""

    probe: Callable[[Settings], Awaitable[None]]


async def _probe_storage(settings: Settings) -> None:
    """List the layout cache container's properties. Read-only, free."""
    from azure.storage.blob.aio import BlobServiceClient

    from fleet_copilot.credentials import get_async_credential

    credential = get_async_credential()
    async with credential:
        client = BlobServiceClient(str(settings.azure_storage_blob_endpoint), credential)
        async with client:
            container = client.get_container_client(settings.azure_layout_cache_container)
            await container.get_container_properties()


async def _probe_document_intelligence(settings: Settings) -> None:
    """List the prebuilt models. A data-plane read that analyses nothing."""
    from azure.ai.documentintelligence.aio import DocumentIntelligenceAdministrationClient

    from fleet_copilot.credentials import get_async_credential

    credential = get_async_credential()
    async with credential:
        client = DocumentIntelligenceAdministrationClient(
            str(settings.azure_document_intelligence_endpoint),
            credential,
            api_version=settings.azure_document_intelligence_api_version,
        )
        async with client:
            async for _ in client.list_models():
                break


async def _probe_openai(settings: Settings) -> None:
    """Embed one word.

    The only data-plane operation this deployment exposes is embedding, so there
    is no read-only probe to make. One token is about $0.00000001.
    """
    from fleet_copilot.ingest.embedding.client import AzureEmbedder

    await AzureEmbedder(settings).embed(["preflight"])


class IndexNotBuiltYetError(RuntimeError):
    """The index does not exist, so document access cannot be proved either way."""


async def _probe_search_service(settings: Settings) -> None:
    """List index names.

    Passes under Subscription Owner, which is the trap: Owner carries
    `Microsoft.Search/*` and that covers index *definitions*. It does not carry
    data actions, so this proves nothing about reading or writing documents --
    that is what the next probe is for.
    """
    from azure.search.documents.indexes.aio import SearchIndexClient

    from fleet_copilot.credentials import get_async_credential

    credential = get_async_credential()
    async with credential:
        client = SearchIndexClient(str(settings.azure_search_endpoint), credential)
        async with client:
            async for _ in client.list_index_names():
                break


async def _probe_search_documents(settings: Settings) -> None:
    """Count documents in the project index. A data action, unlike the above.

    Raises IndexNotBuiltYetError when the index is absent, which is reported as
    skipped rather than denied: not yet built and not allowed are different
    facts, and conflating them would make this check either cry wolf before the
    index exists or stay silent after it does.
    """
    from azure.core.exceptions import ResourceNotFoundError
    from azure.search.documents.aio import SearchClient

    from fleet_copilot.credentials import get_async_credential

    credential = get_async_credential()
    async with credential:
        client = SearchClient(
            str(settings.azure_search_endpoint), settings.azure_search_index, credential
        )
        async with client:
            try:
                await client.get_document_count()
            except ResourceNotFoundError as error:
                msg = f"index {settings.azure_search_index!r} does not exist yet"
                raise IndexNotBuiltYetError(msg) from error


CHECKS: Final[tuple[Check, ...]] = (
    Check(
        name="storage blob (layout cache)",
        role="Storage Blob Data Contributor",
        scope_setting="azure_storage_blob_endpoint",
        probe=_probe_storage,
    ),
    Check(
        name="document intelligence",
        role="Cognitive Services User",
        scope_setting="azure_document_intelligence_endpoint",
        probe=_probe_document_intelligence,
    ),
    Check(
        name="azure openai (embeddings)",
        role="Cognitive Services OpenAI User",
        scope_setting="azure_openai_endpoint",
        probe=_probe_openai,
    ),
    Check(
        name="ai search (index definitions)",
        role="Search Service Contributor",
        scope_setting="azure_search_endpoint",
        probe=_probe_search_service,
    ),
    Check(
        name="ai search (documents)",
        role="Search Index Data Contributor",
        scope_setting="azure_search_endpoint",
        probe=_probe_search_documents,
    ),
)
"""Every data plane this project touches from a laptop.

A new one belongs here in the same commit that first calls it. That is the whole
mitigation: the list is what turns the next missing assignment into a named role
instead of a 401 three layers down a traceback.
"""


async def _run(check: Check, settings: Settings) -> CheckResult:
    if not getattr(settings, check.scope_setting, None):
        return CheckResult(
            name=check.name,
            status=SKIPPED,
            role=check.role,
            detail=f"{check.scope_setting.upper()} not set",
        )
    try:
        await check.probe(settings)
    except IndexNotBuiltYetError as error:
        return CheckResult(name=check.name, status=SKIPPED, role=check.role, detail=str(error))
    except Exception as error:  # noqa: BLE001 - any failure means "cannot reach it"
        return CheckResult(
            name=check.name,
            status=FAILED,
            role=check.role,
            detail=f"{type(error).__name__}",
        )
    return CheckResult(name=check.name, status=OK, role=check.role)


async def run_checks(
    settings: Settings, checks: Sequence[Check] = CHECKS
) -> tuple[CheckResult, ...]:
    """Run every check concurrently and report what each found.

    Concurrent because they are independent and each is a network round trip;
    serially this is the slowest possible way to learn four facts.
    """
    return tuple(await asyncio.gather(*(_run(check, settings) for check in checks)))


def failed(results: Sequence[CheckResult]) -> tuple[CheckResult, ...]:
    """The checks that could not reach their data plane.

    A skipped check is not a failure: an unset endpoint means the stage is not
    configured on this machine, which is a different thing from being denied.
    """
    return tuple(result for result in results if result.status == FAILED)
