"""FastAPI application exposing the pipeline."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fleet_copilot import __version__
from fleet_copilot.api.health import HealthReport, build_report
from fleet_copilot.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Configuration is resolved here rather than per request, so a container with
    missing variables fails at startup instead of serving 500s.
    """
    resolved = load_settings() if settings is None else settings

    app = FastAPI(title="fleet-copilot", version=__version__)

    @app.get(
        "/healthz",
        response_model=HealthReport,
        responses={503: {"model": HealthReport}},
        summary="Dependency health",
    )
    async def healthz() -> JSONResponse:
        report = await build_report(resolved)
        status_code = 200 if report.status == "ok" else 503
        return JSONResponse(status_code=status_code, content=report.model_dump())

    return app
