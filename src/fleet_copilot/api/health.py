"""Dependency checks behind ``GET /healthz``."""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx2
import psycopg
from azure.core.exceptions import ClientAuthenticationError
from pydantic import BaseModel

from fleet_copilot.config import Settings
from fleet_copilot.credentials import get_credential

CheckStatus = Literal["ok", "error", "skipped", "unauthorized"]

COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class DependencyStatus(BaseModel):
    """Outcome of a single dependency check."""

    status: CheckStatus
    detail: str


class HealthReport(BaseModel):
    """Aggregate health of every dependency."""

    status: Literal["ok", "degraded"]
    checks: dict[str, DependencyStatus]


def _vector_extension_version(url: str, connect_timeout: int) -> str | None:
    """The installed pgvector version, or None. Blocking; use to_thread.

    The synchronous driver rather than psycopg's async one, matching the
    embedding store and ingest/cache.py. AsyncConnection refuses to run on
    Windows' default ProactorEventLoop, and because the handler below is
    deliberately broad, that refusal used to be reported as a failed database --
    so /healthz was red on every Windows host whether the database was up or not.
    """
    with psycopg.connect(url, connect_timeout=connect_timeout) as connection:
        row = connection.execute(
            "select extversion from pg_extension where extname = 'vector'"
        ).fetchone()
    return None if row is None else str(row[0])


async def check_database(settings: Settings) -> DependencyStatus:
    """Confirm the database is reachable and has the pgvector extension.

    Reachability alone is not enough: a database without pgvector accepts
    connections and then fails every embedding query, which is a far more
    confusing failure than a red health check.
    """
    try:
        version = await asyncio.to_thread(
            _vector_extension_version,
            settings.database_url,
            int(settings.healthz_timeout_seconds),
        )
    except psycopg.OperationalError as error:
        # The database itself did not answer: down, unreachable, or refusing us.
        return DependencyStatus(status="error", detail=f"unreachable: {error}".strip())
    except Exception as error:
        # Broad by design: a health endpoint must report a failure, never become
        # one. Reported separately from the above because "the database is down"
        # and "the check could not run" are different operational problems, and
        # for a year they rendered identically.
        return DependencyStatus(
            status="error", detail=f"check failed: {type(error).__name__}: {error}"
        )

    if version is None:
        return DependencyStatus(status="error", detail="pgvector extension is not installed")
    return DependencyStatus(status="ok", detail=f"pgvector {version}")


async def check_azure_openai(settings: Settings) -> DependencyStatus:
    """Confirm the Azure OpenAI endpoint is reachable with the current identity.

    Lists models rather than calling a deployment: it exercises the same
    endpoint, api-version and Entra token, and costs nothing.
    """
    if not settings.healthz_check_azure_openai:
        return DependencyStatus(status="skipped", detail="healthz_check_azure_openai is false")
    if settings.azure_openai_endpoint is None:
        return DependencyStatus(status="skipped", detail="azure_openai_endpoint is not set")

    try:
        token = get_credential().get_token(COGNITIVE_SERVICES_SCOPE).token
    except ClientAuthenticationError as error:
        return DependencyStatus(status="unauthorized", detail=f"no Entra token: {error}")

    url = f"{settings.azure_openai_endpoint.rstrip('/')}/openai/models"
    try:
        async with httpx2.AsyncClient(timeout=settings.healthz_timeout_seconds) as client:
            response = await client.get(
                url,
                params={"api-version": settings.azure_openai_api_version},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx2.HTTPError as error:
        return DependencyStatus(status="error", detail=f"{type(error).__name__}: {error}")

    if response.status_code in (401, 403):
        return DependencyStatus(
            status="unauthorized",
            detail="reachable, but the identity lacks Cognitive Services OpenAI User",
        )
    if response.status_code != 200:
        return DependencyStatus(status="error", detail=f"HTTP {response.status_code}")

    deployments = ", ".join(
        [settings.azure_openai_chat_deployment, settings.azure_openai_embedding_deployment]
    )
    return DependencyStatus(status="ok", detail=f"deployments configured: {deployments}")


async def build_report(settings: Settings) -> HealthReport:
    """Run every check and fold the results into one report.

    A skipped check never degrades the service: opting out of the Azure probe is
    the normal state for a laptop with no Entra session.
    """
    checks = {
        "database": await check_database(settings),
        "azure_openai": await check_azure_openai(settings),
    }
    degraded = any(check.status in ("error", "unauthorized") for check in checks.values())
    return HealthReport(status="degraded" if degraded else "ok", checks=checks)
