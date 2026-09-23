"""Index chunks and fetch the ones relevant to a question."""

from fleet_copilot.retrieval.revisions import (
    current_doc_ids,
    family_stem,
    superseded_doc_ids,
)

__all__ = ["current_doc_ids", "family_stem", "superseded_doc_ids"]
