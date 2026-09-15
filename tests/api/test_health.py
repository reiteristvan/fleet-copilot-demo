"""Tests for the /healthz dependency checks."""

from __future__ import annotations

import pytest

from fleet_copilot.api.health import build_report, check_azure_openai, check_database
from fleet_copilot.config import Settings

UNREACHABLE_DSN = "postgresql://nobody:nobody@127.0.0.1:1/absent?connect_timeout=1"


def _settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {"database_url": UNREACHABLE_DSN, "healthz_timeout_seconds": 1.0}
    fields.update(overrides)
    return Settings.model_validate(fields)


@pytest.mark.asyncio
async def test_database_check_reports_failure_instead_of_raising() -> None:
    """A health endpoint must report a broken dependency, never become one."""
    status = await check_database(_settings())

    assert status.status == "error"
    assert status.detail


@pytest.mark.asyncio
async def test_azure_check_skipped_when_disabled() -> None:
    status = await check_azure_openai(_settings(healthz_check_azure_openai=False))

    assert status.status == "skipped"


@pytest.mark.asyncio
async def test_azure_check_skipped_when_endpoint_missing() -> None:
    status = await check_azure_openai(
        _settings(healthz_check_azure_openai=True, azure_openai_endpoint=None)
    )

    assert status.status == "skipped"
    assert "azure_openai_endpoint" in status.detail


@pytest.mark.asyncio
async def test_a_skipped_check_does_not_degrade_the_service() -> None:
    """Opting out of the Azure probe is the normal state on a laptop."""
    report = await build_report(_settings())

    assert report.checks["azure_openai"].status == "skipped"
    # The database is unreachable in this test, so degraded must come from that.
    assert report.checks["database"].status == "error"
    assert report.status == "degraded"
