"""The preflight checks, exercised without touching Azure."""

from __future__ import annotations

import pytest

from fleet_copilot.config import Settings
from fleet_copilot.preflight import (
    CHECKS,
    FAILED,
    OK,
    SKIPPED,
    Check,
    CheckResult,
    IndexNotBuiltYetError,
    failed,
    run_checks,
)


def _settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "database_url": "postgresql://x@localhost/x",
        "azure_openai_endpoint": "https://example.invalid/",
    }
    fields.update(overrides)
    return Settings.model_validate(fields)


async def _succeeds(settings: Settings) -> None:
    return None


async def _denies(settings: Settings) -> None:
    raise PermissionError("Principal does not have access to API/Operation.")


def a_check(probe: object, scope_setting: str = "azure_openai_endpoint") -> Check:
    return Check.model_validate(
        {
            "name": "example plane",
            "role": "Some Data Role",
            "scope_setting": scope_setting,
            "probe": probe,
        }
    )


@pytest.mark.asyncio
async def test_a_reachable_plane_passes() -> None:
    results = await run_checks(_settings(), [a_check(_succeeds)])

    assert [result.status for result in results] == [OK]


@pytest.mark.asyncio
async def test_a_denied_plane_names_the_role_it_needs() -> None:
    """The entire point. Azure's own message names a data action or nothing at
    all, never a role, so the failure reads like a broken token."""
    results = await run_checks(_settings(), [a_check(_denies)])

    assert results[0].status == FAILED
    assert "Some Data Role" in results[0].describe()


@pytest.mark.asyncio
async def test_an_unconfigured_plane_is_skipped_not_failed() -> None:
    """An unset endpoint means the stage is not configured on this machine,
    which is a different thing from being denied by it."""
    results = await run_checks(_settings(azure_openai_endpoint=None), [a_check(_succeeds)])

    assert results[0].status == SKIPPED
    assert failed(results) == ()


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_the_others() -> None:
    """Reporting every gap at once is the difference between one round trip and
    four: a run that stopped at the first would hide the rest."""
    results = await run_checks(_settings(), [a_check(_denies), a_check(_succeeds)])

    assert [result.status for result in results] == [FAILED, OK]
    assert len(failed(results)) == 1


def test_every_data_plane_the_project_touches_is_listed() -> None:
    """A new data plane belongs here in the commit that first calls it.

    Pinned because the list is the whole mitigation: if it drifts, the next
    missing assignment goes back to being a 401 three layers down.
    """
    assert {check.name for check in CHECKS} == {
        "storage blob (layout cache)",
        "document intelligence",
        "azure openai (embeddings)",
        "ai search (index definitions)",
        "ai search (documents)",
    }


def test_search_is_checked_twice_because_it_is_two_grants() -> None:
    """Listing indexes passes under Subscription Owner, which carries
    Microsoft.Search/* and therefore index definitions. Document access is a
    data action and is not covered, so a single check would report a plane as
    reachable that cannot be written to."""
    search = [c for c in CHECKS if c.scope_setting == "azure_search_endpoint"]

    assert {c.role for c in search} == {
        "Search Service Contributor",
        "Search Index Data Contributor",
    }


@pytest.mark.asyncio
async def test_an_index_that_does_not_exist_yet_is_skipped_not_failed() -> None:
    """Not built and not allowed are different facts. Conflating them makes the
    check cry wolf before the index exists, or stay silent after it does."""

    async def _absent(settings: Settings) -> None:
        raise IndexNotBuiltYetError("index 'fleet-chunks' does not exist yet")

    results = await run_checks(_settings(), [a_check(_absent)])

    assert results[0].status == SKIPPED
    assert failed(results) == ()


def test_a_passing_line_does_not_shout_about_a_role() -> None:
    line = CheckResult(name="x", status=OK, role="Some Data Role").describe()

    assert "Some Data Role" not in line
