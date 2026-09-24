"""The embedding cache, over Postgres.

The synchronous driver, called through :func:`asyncio.to_thread`. That is the
pattern `ingest/cache.py` already uses for blob and disk I/O.

Not psycopg's async connection. It refuses to run on Windows' default
ProactorEventLoop and raises an InterfaceError naming the loop. Every local run
would need a global event-loop policy change to make one query.

The usual argument for the async driver is that it does not hold a pool slot
across a round trip. That does not apply here. This opens a connection per call,
so there is no pool to hold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence

import psycopg

from fleet_copilot.ingest.embedding.models import EmbeddingRecord


def _select_known(url: str, hashes: list[str], model_id: str) -> set[str]:
    """Blocking; called through :func:`asyncio.to_thread`."""
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT content_hash FROM ingest.embedding_cache "
            "WHERE model_id = %s AND content_hash = ANY(%s)",
            (model_id, hashes),
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _insert(url: str, rows: list[tuple[str, str, int, str]]) -> None:
    """Blocking; called through :func:`asyncio.to_thread`."""
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO ingest.embedding_cache "
                "(content_hash, model_id, dimensions, embedding) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )
        connection.commit()


class EmbeddingStore:
    """Reads and writes ``ingest.embedding_cache``."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url

    async def known(self, hashes: Collection[str], model_id: str) -> set[str]:
        """Return the subset of ``hashes`` already embedded by ``model_id``.

        One round trip for the whole batch, not one per chunk. At a few hundred
        chunks, the per-query latency dominates everything else the run does.
        """
        if not hashes:
            return set()
        return await asyncio.to_thread(_select_known, self._url, list(hashes), model_id)

    async def put_many(self, records: Sequence[EmbeddingRecord]) -> int:
        """Write ``records``, ignoring any whose key is already present.

        ON CONFLICT DO NOTHING rather than DO UPDATE. The same key under the same
        model is the same vector by definition. A second write is a restart or a
        duplicate chunk, not a correction.
        """
        if not records:
            return 0

        rows = [
            (record.content_hash, record.model_id, record.dimensions, record.literal())
            for record in records
        ]
        await asyncio.to_thread(_insert, self._url, rows)
        return len(records)
