"""Check this machine can reach every Azure data plane the pipeline needs.

    python scripts/preflight.py

Each check makes a real read-only call rather than listing role assignments: an
assignment can exist and still be minutes from propagating, and a listing would
report success the whole time.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import asyncio
import sys

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.preflight import failed, run_checks


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        return 2

    results = asyncio.run(run_checks(settings))
    for result in results:
        print(result.describe())

    problems = failed(results)
    if problems:
        print()
        print(f"{len(problems)} data plane(s) unreachable. Grant the roles named above with:")
        print("  ./infra/deploy.sh dev")
        print("RBAC can take a few minutes to propagate after a deploy.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
