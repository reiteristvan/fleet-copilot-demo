"""What the embedding cache table guarantees."""

from __future__ import annotations

import psycopg
import pytest

DIMENSIONS = 3072
ZERO_VECTOR = "[" + ",".join(["0"] * DIMENSIONS) + "]"
"""The column is vector(3072), so a shorter literal fails on the dimension
rather than on the constraint each test is actually about."""


def test_the_vector_extension_is_enabled(connection: psycopg.Connection) -> None:
    """The image ships pgvector but does not enable it.

    Without the extension the column type is rejected as unknown, which reads
    like a typo in the migration rather than a missing CREATE EXTENSION.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None


def test_a_non_sha256_hash_is_refused(connection: psycopg.Connection) -> None:
    """The key is a content hash; anything else in it is a bug upstream that
    would otherwise sit in the table looking like data."""
    with connection.cursor() as cursor, pytest.raises(psycopg.errors.CheckViolation):
        cursor.execute(
            "INSERT INTO ingest.embedding_cache "
            "(content_hash, model_id, dimensions, embedding) VALUES (%s, %s, %s, %s)",
            ("not-a-hash", "text-embedding-3-large", DIMENSIONS, ZERO_VECTOR),
        )


def test_the_same_hash_under_two_models_is_two_rows(connection: psycopg.Connection) -> None:
    """A model change must miss the cache, not return another model's vectors."""
    with connection.cursor() as cursor:
        for model in ("model-a", "model-b"):
            cursor.execute(
                "INSERT INTO ingest.embedding_cache "
                "(content_hash, model_id, dimensions, embedding) VALUES (%s, %s, %s, %s)",
                ("a" * 64, model, DIMENSIONS, ZERO_VECTOR),
            )
        cursor.execute(
            "SELECT count(*) FROM ingest.embedding_cache WHERE content_hash = %s", ("a" * 64,)
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 2


def test_the_readonly_role_cannot_read_vectors(readonly_connection: psycopg.Connection) -> None:
    """ADR 0002's boundary is only real if every new object is granted
    deliberately. copilot_ro answers questions and has no business here."""
    denied = pytest.raises(psycopg.errors.InsufficientPrivilege)
    with readonly_connection.cursor() as cursor, denied:
        cursor.execute("SELECT count(*) FROM ingest.embedding_cache")
