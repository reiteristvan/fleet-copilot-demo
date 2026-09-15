"""Command-line entry point for the ``fleet-copilot`` console script."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from fleet_copilot import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the requested command."""
    build_parser().parse_args(argv)
    print(f"fleet-copilot {__version__}: ingest -> retrieval -> agents -> evals -> api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
