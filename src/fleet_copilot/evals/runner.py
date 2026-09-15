"""Entry point for the offline evaluation suite."""

from __future__ import annotations

import sys


def discover_suites() -> set[str]:
    """Return the names of the registered eval suites.

    No suites are registered yet; the harness exists so that ``just eval``
    is wired up from the first commit rather than bolted on later.
    """
    return set()


def main() -> int:
    """Run every registered eval suite and report the aggregate result."""
    suites = discover_suites()
    for name in sorted(suites):
        print(f"eval suite: {name}")
    print(f"{len(suites)} eval suite(s) completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
