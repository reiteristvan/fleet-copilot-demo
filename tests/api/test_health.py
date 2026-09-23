"""Tests for the /healthz dependency checks."""

from __future__ import annotations

import pytest

from fleet_copilot.api import health
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
async def test_an_unreachable_database_says_so() -> None:
    """Distinguished from a check that could not run at all.

    Both used to render identically, which mattered because psycopg's async
    driver refuses Windows' default event loop: /healthz was red on every
    Windows host whether the database was up or not, and the detail gave no way
    to tell that from a genuine outage.
    """
    status = await check_database(_settings())

    assert status.detail.startswith("unreachable:")


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_is_not_reported_as_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver or configuration fault is an operational problem of its own."""

    def _explode(url: str, connect_timeout: int) -> str | None:
        raise RuntimeError("driver cannot use this event loop")

    monkeypatch.setattr(health, "_vector_extension_version", _explode)

    status = await check_database(_settings())

    assert status.status == "error"
    assert status.detail.startswith("check failed: RuntimeError")
    assert "unreachable" not in status.detail


@pytest.mark.asyncio
async def test_a_live_database_reports_ok_with_its_pgvector_version(
    database_url: str,
) -> None:
    """The green path, which nothing covered: every database assertion here was
    about a failure, so a check that could never succeed would have passed."""
    status = await check_database(_settings(database_url=database_url))

    assert status.status == "ok"
    assert status.detail.startswith("pgvector ")


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
