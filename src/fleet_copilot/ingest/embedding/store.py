"""The embedding cache, over Postgres.

The synchronous driver, called through :func:`asyncio.to_thread`, which is the
pattern `ingest/cache.py` already uses for blob and disk I/O.

Not psycopg's async connection: it refuses to run on Windows' default
ProactorEventLoop and raises an InterfaceError naming the loop, so every local
run would need a global event-loop policy change to use one query. The usual
argument for the async driver -- not holding a pool slot for a round trip --
does not apply here, because this opens a connection per call and there is no
pool to hold.
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

        One round trip for the whole batch rather than one per chunk: at a few
        hundred chunks the per-query latency dominates everything else the run
        does.
        """
        if not hashes:
            return set()
        return await asyncio.to_thread(_select_known, self._url, list(hashes), model_id)

    async def put_many(self, records: Sequence[EmbeddingRecord]) -> int:
        """Write ``records``, ignoring any whose key is already present.

        ON CONFLICT DO NOTHING rather than DO UPDATE: the same key under the
        same model is by definition the same vector, so a second write is a
        restart or a duplicate chunk, not a correction.
        """
        if not records:
            return 0

        rows = [
            (record.content_hash, record.model_id, record.dimensions, record.literal())
            for record in records
        ]
        await asyncio.to_thread(_insert, self._url, rows)
        return len(records)
