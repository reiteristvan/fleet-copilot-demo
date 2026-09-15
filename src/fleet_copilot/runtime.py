"""Process-level setup that must happen before an event loop exists."""

from __future__ import annotations

import asyncio
import sys


def configure_event_loop_policy() -> None:
    """Select an event loop that psycopg's async mode can use.

    Windows defaults to the ProactorEventLoop, which psycopg rejects outright
    with ``InterfaceError``; the container runs Linux and never sees this, so
    without the fix the API works in compose and fails on the machine it is
    developed on. Must run before the first loop is created.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
