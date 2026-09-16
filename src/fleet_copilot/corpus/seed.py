"""Read the seed data in ``data/`` and validate it before anything is generated.

Three kinds of file feed the generator: the machine catalogue, the corpus spec,
and one fragment file per document type holding the interchangeable phrases the
prose is assembled from. All of them are read here, so a malformed file fails
once with a path and a field name rather than as a ``KeyError`` thrown from
inside the planner with no indication of which file was wrong.

Nothing here is a coroutine, so the blocking reads are blocking on purpose:
generation is a batch script, not a request path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fleet_copilot.corpus.models import Catalogue, CorpusSpec, DocumentType, Language

DEFAULT_DATA_DIR: Final = Path(__file__).resolve().parents[3] / "data"
"""The repository's ``data/`` directory, found relative to this file.

Resolved from ``__file__`` rather than the working directory so that generation
produces the same corpus whether it is run from the repository root, from a
test, or from somewhere else entirely.
"""

NonEmptyStr = Annotated[str, Field(min_length=1)]


class CorpusDataError(RuntimeError):
    """Raised when a seed data file is missing, unreadable or malformed."""


class FragmentSet(BaseModel):
    """The named phrase banks available for one document type in one language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    banks: dict[str, Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]]

    def bank(self, name: str) -> tuple[str, ...]:
        """Return the phrases in ``name``.

        The error names the banks that do exist. A missing bank is almost always
        a typo in a fragment file or a planner referring to a bank that was
        renamed, and both are far quicker to fix with the available names in
        front of you than with a bare KeyError.
        """
        try:
            return self.banks[name]
        except KeyError:
            msg = f"no fragment bank named {name!r}; available: {sorted(self.banks)}"
            raise KeyError(msg) from None


class FragmentFile(BaseModel):
    """One ``data/fragments/<type>.yaml`` file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: DocumentType
    en: FragmentSet
    hu: FragmentSet | None = None

    def for_language(self, language: Language) -> FragmentSet:
        """Return the phrase banks for ``language``.

        Falling back to English for a document type with no Hungarian banks
        would produce a document whose front matter claims ``language: hu`` over
        English prose -- which is worse than failing, because cross-language
        retrieval would then be measured against a corpus that lies about what
        language its documents are in.
        """
        if language is Language.EN:
            return self.en
        if self.hu is None:
            msg = f"{self.type.value} has no Hungarian fragments"
            raise CorpusDataError(msg)
        return self.hu


class Fragments(BaseModel):
    """Every fragment file, keyed by document type."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: dict[DocumentType, FragmentFile]

    def of(self, document_type: DocumentType, language: Language) -> FragmentSet:
        """Return the phrase banks for ``document_type`` in ``language``."""
        try:
            file = self.files[document_type]
        except KeyError:
            msg = f"no fragment file for {document_type.value}"
            raise CorpusDataError(msg) from None
        return file.for_language(language)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse ``path`` as a YAML mapping, or raise :class:`CorpusDataError`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        msg = f"cannot read {path}: {error}"
        raise CorpusDataError(msg) from error

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        msg = f"{path} is not valid YAML: {error}"
        raise CorpusDataError(msg) from error

    if not isinstance(parsed, dict):
        msg = f"{path} must contain a mapping at the top level, found {type(parsed).__name__}"
        raise CorpusDataError(msg)
    return parsed


def _validate[ModelT: BaseModel](model: type[ModelT], data: dict[str, Any], path: Path) -> ModelT:
    """Validate ``data`` against ``model``, naming ``path`` if it fails."""
    try:
        return model.model_validate(data)
    except ValidationError as error:
        msg = f"{path} is not a valid {model.__name__}:\n{error}"
        raise CorpusDataError(msg) from error


def load_catalogue(path: Path | None = None) -> Catalogue:
    """Read and validate ``data/catalogue.yaml``."""
    resolved = DEFAULT_DATA_DIR / "catalogue.yaml" if path is None else path
    return _validate(Catalogue, _read_yaml(resolved), resolved)


def load_corpus_spec(path: Path | None = None) -> CorpusSpec:
    """Read and validate ``data/corpus_spec.yaml``."""
    resolved = DEFAULT_DATA_DIR / "corpus_spec.yaml" if path is None else path
    return _validate(CorpusSpec, _read_yaml(resolved), resolved)


def load_fragments(directory: Path | None = None) -> Fragments:
    """Read every ``*.yaml`` in ``data/fragments/``.

    A fragment file whose ``type`` does not match its filename is rejected: the
    planner looks documents up by type and a human looks them up by filename, so
    the two disagreeing means one of them is reading a file that is not the one
    they think it is.
    """
    resolved = DEFAULT_DATA_DIR / "fragments" if directory is None else directory
    if not resolved.is_dir():
        msg = f"fragment directory {resolved} does not exist"
        raise CorpusDataError(msg)

    files: dict[DocumentType, FragmentFile] = {}
    for path in sorted(resolved.glob("*.yaml")):
        fragment = _validate(FragmentFile, _read_yaml(path), path)
        if fragment.type.value != path.stem:
            msg = f"{path} declares type {fragment.type.value!r} but is named {path.stem!r}"
            raise CorpusDataError(msg)
        files[fragment.type] = fragment

    if not files:
        msg = f"no fragment files found in {resolved}"
        raise CorpusDataError(msg)
    return Fragments(files=files)
