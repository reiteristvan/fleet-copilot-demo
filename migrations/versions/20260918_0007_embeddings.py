"""Cache embeddings by the hash of what was actually embedded.

The corpus is deterministic and the chunkers are pure, so the same chunk
produces the same vector on every run. The acceptance criterion for this story
is that a clean re-run makes zero embedding calls -- which is a statement about
determinism rather than about cost: at roughly 112,000 tokens a full run costs
about a cent and a half, and three chunking strategies are about to be compared
on inputs that must not move between runs.

No vector index. Every read here is an equality match on the primary key;
similarity search belongs to the retrieval story, which will have to choose
halfvec or a reduced-dimension copy because pgvector's hnsw and ivfflat cap at
2000 dimensions and these are 3072 (ADR 0007).

Revision ID: 0007_embeddings
Revises: 0006_as_of_function
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_embeddings"
down_revision: str | None = "0006_as_of_function"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READONLY_ROLE = "copilot_ro"


def upgrade() -> None:
    # The pgvector image ships the extension but does not enable it in the
    # database. Without this the column type below is rejected as an unknown
    # type, which reads like a typo rather than a missing extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("CREATE SCHEMA IF NOT EXISTS ingest")

    op.execute(
        """
        CREATE TABLE ingest.embedding_cache (
            content_hash  text        NOT NULL,
            model_id      text        NOT NULL,
            dimensions    integer     NOT NULL,
            embedding     vector(3072) NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT embedding_cache_pkey PRIMARY KEY (content_hash, model_id),
            CONSTRAINT embedding_cache_hash_is_sha256
                CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT embedding_cache_dimensions_positive
                CHECK (dimensions > 0)
        )
        """
    )

    # The model is in the key, not only in a column: a different deployment must
    # miss and re-embed rather than silently return vectors from another model,
    # whose numbers would look entirely reasonable and rank nothing correctly.
    op.execute(
        """
        COMMENT ON TABLE ingest.embedding_cache IS
        'Vectors keyed by the sha256 of embed_text and the model that produced them.'
        """
    )

    # copilot_ro answers questions; it has no business reading raw vectors, and
    # ADR 0002's boundary is only real if every new object is granted
    # deliberately rather than by default.
    op.execute(f"REVOKE ALL ON SCHEMA ingest FROM {READONLY_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingest.embedding_cache")
    # The schema and the extension are left in place. Another migration may have
    # put something in the schema, and dropping an extension that a different
    # table's column type depends on fails in a way that is hard to read.
