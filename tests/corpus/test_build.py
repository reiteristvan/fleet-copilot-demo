"""The corpus committed to ``data/`` is the one the seed data produces.

This is the test that makes the manifest worth committing. It regenerates the
whole corpus into a temporary directory and compares hashes, so a change to a
fragment bank, the catalogue or a renderer that nobody regenerated for fails
here rather than being discovered when a retrieval metric moves for no visible
reason.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fleet_copilot.corpus.build import check, corpus_root, manifest_path, plan
from fleet_copilot.corpus.manifest import Manifest, load_manifest
from fleet_copilot.corpus.models import Language, OutputFormat
from fleet_copilot.corpus.writer import MARKDOWN_DIR, PUBLISHED_DIR, write_corpus

EXPECTED_PLANTED = {
    "conflict-battery-charging-temp",
    "contradiction-brush-wear-limit",
    "injection-handover",
    "injection-scanned-pdf",
    "injection-service-report",
}


def committed() -> Manifest:
    """The manifest as committed to the repository."""
    return load_manifest(manifest_path())


class TestCommittedCorpus:
    def test_the_manifest_exists_and_is_valid(self) -> None:
        assert committed().total == 120

    def test_regenerating_reproduces_the_committed_corpus(self) -> None:
        """The whole point of generating offline instead of with a model."""
        assert check() == []

    def test_every_manifest_path_exists_on_disk(self) -> None:
        root = corpus_root()
        for entry in committed().documents:
            assert (root / entry.path).is_file(), entry.path

    def test_markdown_is_written_for_every_document_including_converted_ones(self) -> None:
        """A PDF diff says nothing about what a fragment change did, so the
        Markdown is kept as the reviewable source of truth for all 120."""
        markdown = corpus_root() / MARKDOWN_DIR
        for entry in committed().documents:
            assert (markdown / f"{entry.doc_id}.md").is_file(), entry.doc_id

    def test_a_converted_document_is_published_only_once(self) -> None:
        """ADR 0003: uploading the Markdown and the PDF of one manual would
        plant an exact-duplicate pair nobody intended."""
        for entry in committed().documents:
            expected_dir = PUBLISHED_DIR if entry.is_converted else MARKDOWN_DIR
            assert entry.path.startswith(f"{expected_dir}/"), entry.path

    def test_the_published_set_spans_three_formats(self) -> None:
        formats = {entry.format for entry in committed().documents}
        assert formats == set(OutputFormat)

    def test_the_published_set_spans_two_languages(self) -> None:
        assert {entry.language for entry in committed().documents} == {Language.EN, Language.HU}


class TestPlantedCasesAreNameable:
    def test_every_planted_case_can_be_named_from_the_manifest(self) -> None:
        """The manifest is the only place these are recorded, so this is the
        only way to find them."""
        assert set(committed().planted_ids()) == EXPECTED_PLANTED

    def test_every_planted_entry_carries_an_actionable_note(self) -> None:
        for entry in committed().planted():
            assert entry.planted_note
            assert len(entry.planted_note) > 40, entry.doc_id

    def test_the_conflict_names_two_documents(self) -> None:
        pair = [
            e for e in committed().planted() if e.planted_id == "conflict-battery-charging-temp"
        ]
        assert len(pair) == 2

    def test_the_ocr_only_injection_is_recorded_as_a_pdf(self) -> None:
        entries = [e for e in committed().planted() if e.planted_id == "injection-scanned-pdf"]
        assert [e.format for e in entries] == [OutputFormat.SCANNED_PDF]

    def test_no_document_file_admits_to_being_planted(self) -> None:
        """A planted marker in the document would be indexed with it, and every
        one of these cases would become solvable by a metadata filter."""
        root = corpus_root()
        for entry in committed().planted():
            if entry.format is not OutputFormat.MARKDOWN:
                continue
            text = (root / entry.path).read_text(encoding="utf-8")
            assert "planted" not in text.lower()
            # Guaranteed by ManifestEntry's validator; mypy cannot see that.
            assert entry.planted_id is not None
            assert entry.planted_id not in text


class TestWriteCorpus:
    def test_writes_the_expected_layout_into_a_fresh_directory(self, tmp_path: Path) -> None:
        documents = plan()
        manifest = write_corpus(documents, tmp_path, seed=committed().seed)
        assert manifest.total == len(documents)
        assert len(list((tmp_path / MARKDOWN_DIR).glob("*.md"))) == len(documents)
        converted = [doc for doc in documents if doc.output_format is not OutputFormat.MARKDOWN]
        assert len(list((tmp_path / PUBLISHED_DIR).iterdir())) == len(converted)

    def test_markdown_files_keep_line_feeds_on_every_platform(self, tmp_path: Path) -> None:
        """write_text would translate these to CRLF on Windows and give the same
        corpus a different hash there than in CI."""
        write_corpus(plan(), tmp_path, seed=1)
        for path in sorted((tmp_path / MARKDOWN_DIR).glob("*.md"))[:5]:
            assert b"\r\n" not in path.read_bytes(), path.name

    def test_writing_twice_produces_identical_bytes(self, tmp_path: Path) -> None:
        first = write_corpus(plan(), tmp_path / "a", seed=1)
        second = write_corpus(plan(), tmp_path / "b", seed=1)
        assert first.documents == second.documents


class TestCloneCompleteness:
    """What a fresh clone gets, as opposed to what is on this working tree.

    The two differ whenever a path is ignored, and an ignored path fails
    silently: the working tree is complete, every test passes against it, and
    the manifest is correct. Only somebody cloning the repository finds out.
    This happened once already, to the whole converted subset, because the
    repository's stock Python .gitignore ignores ``dist/``.
    """

    def _tracked(self) -> set[str] | None:
        """Paths git tracks under the corpus, or None outside a git checkout."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--", "data/corpus"],
                capture_output=True,
                text=True,
                check=True,
                cwd=Path(__file__).resolve().parents[2],
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return {line for line in result.stdout.splitlines() if line}

    def test_every_published_document_is_tracked_by_git(self) -> None:
        tracked = self._tracked()
        if tracked is None:
            pytest.skip("not a git checkout")
        missing = [
            entry.path
            for entry in committed().documents
            if f"data/corpus/{entry.path}" not in tracked
        ]
        assert not missing, f"{len(missing)} published documents are not in git: {missing[:5]}"

    def test_the_manifest_itself_is_tracked(self) -> None:
        tracked = self._tracked()
        if tracked is None:
            pytest.skip("not a git checkout")
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "ls-files", "--", "data/manifest.json"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert result.stdout.strip() == "data/manifest.json"

    def test_all_three_formats_survive_into_the_clone(self) -> None:
        """The failure mode was losing two formats entirely while the corpus
        still looked complete locally."""
        tracked = self._tracked()
        if tracked is None:
            pytest.skip("not a git checkout")
        suffixes = {Path(path).suffix for path in tracked}
        assert {".md", ".pdf", ".docx"} <= suffixes
