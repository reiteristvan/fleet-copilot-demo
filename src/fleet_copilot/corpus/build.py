"""Generate the corpus, and check that a regeneration reproduces it.

The orchestration lives here rather than in ``scripts/gen_corpus.py`` because
``[tool.mypy] files`` covers ``src`` and ``tests`` and not ``scripts``. A script
holding logic is a script the gate does not check, so the script parses
arguments and calls in here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fleet_copilot.corpus.manifest import Manifest, load_manifest, verify_manifest
from fleet_copilot.corpus.planner import PlannedDocument, plan_corpus
from fleet_copilot.corpus.seed import (
    DEFAULT_DATA_DIR,
    load_catalogue,
    load_corpus_spec,
    load_fragments,
)
from fleet_copilot.corpus.writer import write_corpus

CORPUS_DIRNAME = "corpus"
MANIFEST_FILENAME = "manifest.json"


def corpus_root(data_dir: Path | None = None) -> Path:
    """The directory the generated documents live in."""
    return (data_dir or DEFAULT_DATA_DIR) / CORPUS_DIRNAME


def manifest_path(data_dir: Path | None = None) -> Path:
    """The path of the committed manifest."""
    return (data_dir or DEFAULT_DATA_DIR) / MANIFEST_FILENAME


def plan(data_dir: Path | None = None) -> tuple[PlannedDocument, ...]:
    """Load the seed data and plan the corpus, without writing anything."""
    resolved = data_dir or DEFAULT_DATA_DIR
    return plan_corpus(
        load_catalogue(resolved / "catalogue.yaml"),
        load_corpus_spec(resolved / "corpus_spec.yaml"),
        load_fragments(resolved / "fragments"),
    )


def build(data_dir: Path | None = None) -> Manifest:
    """Generate the corpus into ``data/corpus`` and write ``data/manifest.json``."""
    resolved = data_dir or DEFAULT_DATA_DIR
    documents = plan(resolved)
    spec_seed = load_corpus_spec(resolved / "corpus_spec.yaml").seed
    manifest = write_corpus(documents, corpus_root(resolved), seed=spec_seed)
    path = manifest_path(resolved)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(manifest.to_json().encode("utf-8"))
    return manifest


def check(data_dir: Path | None = None) -> list[str]:
    """Return every reason the corpus on disk is not the committed one.

    Two separate questions, because they fail for different reasons and a
    caller wants to know which. Do the files on disk still hash to what the
    manifest says -- that is, has anyone hand-edited a generated document? And
    does regenerating from the seed data reproduce those same hashes -- that is,
    has a fragment, the catalogue or a renderer changed without the corpus being
    regenerated?

    Returns problems rather than raising so one run reports all of them.
    """
    resolved = data_dir or DEFAULT_DATA_DIR
    path = manifest_path(resolved)
    if not path.is_file():
        return [f"no manifest at {path}; run the generator"]

    committed = load_manifest(path)
    problems = verify_manifest(committed, corpus_root(resolved))

    with tempfile.TemporaryDirectory() as scratch:
        documents = plan(resolved)
        seed = load_corpus_spec(resolved / "corpus_spec.yaml").seed
        regenerated = write_corpus(documents, Path(scratch), seed=seed)

    if regenerated.documents != committed.documents:
        problems.extend(_describe_drift(committed, regenerated))
    return problems


def _describe_drift(committed: Manifest, regenerated: Manifest) -> list[str]:
    """Name the documents a regeneration would change, added or removed."""
    before = {entry.doc_id: entry for entry in committed.documents}
    after = {entry.doc_id: entry for entry in regenerated.documents}

    problems = [f"regenerating would add {doc_id}" for doc_id in sorted(after.keys() - before)]
    problems.extend(
        f"regenerating would remove {doc_id}" for doc_id in sorted(before.keys() - after)
    )
    problems.extend(
        f"regenerating would change {doc_id}"
        for doc_id in sorted(before.keys() & after.keys())
        if before[doc_id] != after[doc_id]
    )
    return problems
