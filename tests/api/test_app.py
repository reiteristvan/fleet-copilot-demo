"""Tests for the FastAPI application."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fleet_copilot.api.app import create_app
from fleet_copilot.config import Settings

UNREACHABLE_DSN = "postgresql://nobody:nobody@127.0.0.1:1/absent?connect_timeout=1"


def test_healthz_returns_503_and_a_dependency_report_when_a_check_fails() -> None:
    settings = Settings.model_validate(
        {"database_url": UNREACHABLE_DSN, "healthz_timeout_seconds": 1.0}
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert set(body["checks"]) == {"database", "azure_openai"}
    assert body["checks"]["database"]["status"] == "error"
