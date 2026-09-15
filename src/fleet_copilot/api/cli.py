"""Command-line entry point for the ``fleet-copilot`` console script."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from fleet_copilot import __version__
from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.runtime import configure_event_loop_policy


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="fleet-copilot",
        description="Run the fleet copilot pipeline.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fleet-copilot {__version__}",
    )

    subcommands = parser.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="Run the HTTP API.")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the requested command."""
    arguments = build_parser().parse_args(argv)

    if arguments.command == "serve":
        # Resolved here rather than left to uvicorn's app factory, so a missing
        # variable is one line on stderr instead of a traceback that reads like
        # a crash.
        try:
            load_settings()
        except SettingsError as error:
            print(error, file=sys.stderr)
            return 2

        configure_event_loop_policy()

        import uvicorn

        uvicorn.run(
            "fleet_copilot.api.app:create_app",
            factory=True,
            host=arguments.host,
            port=arguments.port,
        )
        return 0

    print(f"fleet-copilot {__version__}: ingest -> retrieval -> agents -> evals -> api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
