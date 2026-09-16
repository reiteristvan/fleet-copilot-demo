"""The record of what the corpus contains, and what is deliberately wrong in it.

``data/manifest.json`` is committed. It lists every uploaded file with its
content hash, and it is the only place a planted case is named -- ADR 0003
keeps those out of the documents themselves, because a planted flag in indexed
metadata would make every injection and conflict eval solvable by a filter.

The manifest carries no generation timestamp. A timestamp would change the file
on every run and make ``git diff`` useless for the one question it should
answer: did the corpus actually change? Git already records when.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fleet_copilot.corpus.models import DocumentType, Language, OutputFormat, PlantedKind
from fleet_copilot.corpus.seed import CorpusDataError

MANIFEST_VERSION: Final = 1
"""Schema version of ``data/manifest.json``.

Bumped when the entry shape changes, so a reader can tell an old manifest from
a corrupt one.
"""


def sha256_of(data: bytes) -> str:
    """Return the SHA-256 of ``data`` as hex."""
    return hashlib.sha256(data).hexdigest()


class ManifestEntry(BaseModel):
    """One file in the corpus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Annotated[str, Field(min_length=1)]
    type: DocumentType
    language: Language
    format: OutputFormat
    path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: Annotated[int, Field(gt=0)]
    planted: bool = False
    planted_kind: PlantedKind | None = None
    planted_id: str | None = None
    planted_note: str | None = None

    @model_validator(mode="after")
    def _planted_entries_say_what_was_planted(self) -> Self:
        """Keep ``planted`` and the three detail fields in step.

        An entry flagged planted with no note is a defect nobody can act on,
        and a note on an unflagged entry is invisible to anything filtering on
        the flag -- which is how the manifest is meant to be read.
        """
        details = (self.planted_kind, self.planted_id, self.planted_note)
        if self.planted and any(detail is None for detail in details):
            msg = f"{self.doc_id} is flagged planted but does not say what was planted"
            raise ValueError(msg)
        if not self.planted and any(detail is not None for detail in details):
            msg = f"{self.doc_id} carries planted details but is not flagged planted"
            raise ValueError(msg)
        return self

    @property
    def is_converted(self) -> bool:
        """Whether this document was written out as something other than Markdown."""
        return self.format is not OutputFormat.MARKDOWN


class Manifest(BaseModel):
    """The whole of ``data/manifest.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: int = MANIFEST_VERSION
    seed: int
    total: Annotated[int, Field(gt=0)]
    documents: Annotated[tuple[ManifestEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _entries_are_consistent(self) -> Self:
        """Reject a manifest that disagrees with itself."""
        if len(self.documents) != self.total:
            msg = f"manifest lists {len(self.documents)} documents but total is {self.total}"
            raise ValueError(msg)

        identifiers = [entry.doc_id for entry in self.documents]
        if len(set(identifiers)) != len(identifiers):
            msg = "manifest lists a doc_id more than once"
            raise ValueError(msg)

        paths = [entry.path for entry in self.documents]
        if len(set(paths)) != len(paths):
            msg = "manifest lists a path more than once"
            raise ValueError(msg)
        return self

    def planted(self) -> tuple[ManifestEntry, ...]:
        """Every entry carrying a deliberate defect."""
        return tuple(entry for entry in self.documents if entry.planted)

    def planted_ids(self) -> tuple[str, ...]:
        """The distinct planted case identifiers, in sorted order."""
        return tuple(sorted({entry.planted_id or "" for entry in self.planted()}))

    def to_json(self) -> str:
        """Serialise deterministically, ending in a single newline.

        ``ensure_ascii=False`` so the Hungarian in a planted note stays readable
        in a diff rather than becoming a wall of escape sequences.
        """
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def load_manifest(path: Path) -> Manifest:
    """Read and validate a manifest file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        msg = f"cannot read {path}: {error}"
        raise CorpusDataError(msg) from error
    try:
        return Manifest.model_validate_json(raw)
    except ValidationError as error:
        msg = f"{path} is not a valid manifest:\n{error}"
        raise CorpusDataError(msg) from error


def verify_manifest(manifest: Manifest, root: Path) -> list[str]:
    """Re-hash every file and return a problem per line, empty if all match.

    Returns problems rather than raising so one run reports every mismatch. A
    corpus regenerated after a fragment edit will differ in many files at once,
    and being told about them one run at a time is how that becomes tedious
    enough to skip.
    """
    problems: list[str] = []
    for entry in manifest.documents:
        path = root / entry.path
        if not path.is_file():
            problems.append(f"{entry.doc_id}: missing file {entry.path}")
            continue
        data = path.read_bytes()
        if len(data) != entry.bytes:
            problems.append(
                f"{entry.doc_id}: {entry.path} is {len(data)} bytes, manifest says {entry.bytes}"
            )
        actual = sha256_of(data)
        if actual != entry.sha256:
            problems.append(
                f"{entry.doc_id}: {entry.path} hashes to {actual[:12]}..., "
                f"manifest says {entry.sha256[:12]}..."
            )
    return problems
