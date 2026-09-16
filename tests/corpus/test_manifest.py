"""The manifest models, and what they refuse to record."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fleet_copilot.corpus.manifest import (
    MANIFEST_VERSION,
    Manifest,
    ManifestEntry,
    load_manifest,
    sha256_of,
    verify_manifest,
)
from fleet_copilot.corpus.models import OutputFormat
from fleet_copilot.corpus.seed import CorpusDataError

BODY = b"# Battery care\n"


def entry_data(**overrides: Any) -> dict[str, Any]:
    """A valid manifest entry payload."""
    data: dict[str, Any] = {
        "doc_id": "sd-50b-maint-battery-care-rev4",
        "type": "maintenance_procedure",
        "language": "en",
        "format": "markdown",
        "path": "markdown/sd-50b-maint-battery-care-rev4.md",
        "sha256": sha256_of(BODY),
        "bytes": len(BODY),
    }
    return data | overrides


def manifest_data(**overrides: Any) -> dict[str, Any]:
    """A valid manifest payload with a single entry."""
    data: dict[str, Any] = {"seed": 20260916, "total": 1, "documents": [entry_data()]}
    return data | overrides


class TestManifestEntry:
    def test_accepts_a_plain_entry(self) -> None:
        entry = ManifestEntry.model_validate(entry_data())
        assert entry.planted is False
        assert entry.is_converted is False

    def test_a_converted_entry_knows_it(self) -> None:
        entry = ManifestEntry.model_validate(
            entry_data(format="scanned_pdf", path="published/x.pdf")
        )
        assert entry.is_converted is True
        assert entry.format is OutputFormat.SCANNED_PDF

    def test_rejects_a_planted_flag_with_no_detail(self) -> None:
        """A flagged defect nobody can act on is worse than an unflagged one."""
        with pytest.raises(ValidationError, match="does not say what was planted"):
            ManifestEntry.model_validate(entry_data(planted=True))

    def test_rejects_detail_without_the_flag(self) -> None:
        """The flag is how the manifest is filtered, so detail behind an unset
        flag is detail nothing will ever find."""
        payload = entry_data(
            planted_kind="prompt_injection",
            planted_id="injection-handover",
            planted_note="buried mid-document",
        )
        with pytest.raises(ValidationError, match="not flagged planted"):
            ManifestEntry.model_validate(payload)

    def test_accepts_a_fully_described_planted_entry(self) -> None:
        payload = entry_data(
            planted=True,
            planted_kind="prompt_injection",
            planted_id="injection-handover",
            planted_note="buried mid-document",
        )
        assert ManifestEntry.model_validate(payload).planted_id == "injection-handover"

    @pytest.mark.parametrize("bad", ["", "not-hex", "AB" * 32, sha256_of(BODY)[:-1]])
    def test_rejects_a_malformed_hash(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ManifestEntry.model_validate(entry_data(sha256=bad))

    def test_rejects_an_empty_file(self) -> None:
        with pytest.raises(ValidationError):
            ManifestEntry.model_validate(entry_data(bytes=0))


class TestManifest:
    def test_rejects_a_total_that_disagrees_with_the_entries(self) -> None:
        with pytest.raises(ValidationError, match="but total is"):
            Manifest.model_validate(manifest_data(total=2))

    def test_rejects_a_repeated_doc_id(self) -> None:
        payload = manifest_data(
            total=2, documents=[entry_data(), entry_data(path="published/other.pdf")]
        )
        with pytest.raises(ValidationError, match="doc_id more than once"):
            Manifest.model_validate(payload)

    def test_rejects_two_entries_writing_to_one_path(self) -> None:
        """The second would silently overwrite the first on disk."""
        payload = manifest_data(total=2, documents=[entry_data(), entry_data(doc_id="other")])
        with pytest.raises(ValidationError, match="path more than once"):
            Manifest.model_validate(payload)

    def test_records_its_schema_version(self) -> None:
        assert Manifest.model_validate(manifest_data()).manifest_version == MANIFEST_VERSION

    def test_planted_helpers_name_the_cases(self) -> None:
        planted = entry_data(
            doc_id="handover-x",
            path="markdown/handover-x.md",
            planted=True,
            planted_kind="prompt_injection",
            planted_id="injection-handover",
            planted_note="buried",
        )
        manifest = Manifest.model_validate(
            manifest_data(total=2, documents=[entry_data(), planted])
        )
        assert len(manifest.planted()) == 1
        assert manifest.planted_ids() == ("injection-handover",)


class TestSerialisation:
    def test_json_is_stable_and_ends_in_one_newline(self) -> None:
        manifest = Manifest.model_validate(manifest_data())
        rendered = manifest.to_json()
        assert rendered == manifest.to_json()
        assert rendered.endswith("\n")
        assert not rendered.endswith("\n\n")

    def test_json_omits_unset_planted_fields(self) -> None:
        """Three nulls on every one of 120 entries is noise in a committed file."""
        payload = json.loads(Manifest.model_validate(manifest_data()).to_json())
        assert "planted_kind" not in payload["documents"][0]

    def test_json_keeps_non_ascii_readable(self) -> None:
        """So a Hungarian planted note is legible in a diff."""
        planted = entry_data(
            planted=True,
            planted_kind="handover_contradiction",
            planted_id="contradiction-brush-wear-limit",
            planted_note="A kefék kopáshatára",
        )
        rendered = Manifest.model_validate(manifest_data(documents=[planted])).to_json()
        assert "kopáshatára" in rendered

    def test_round_trips_through_a_file(self, tmp_path: Path) -> None:
        manifest = Manifest.model_validate(manifest_data())
        path = tmp_path / "manifest.json"
        path.write_text(manifest.to_json(), encoding="utf-8")
        assert load_manifest(path) == manifest

    def test_reports_a_missing_manifest_with_its_path(self, tmp_path: Path) -> None:
        with pytest.raises(CorpusDataError, match="cannot read"):
            load_manifest(tmp_path / "absent.json")

    def test_reports_a_malformed_manifest(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"seed": 1}', encoding="utf-8")
        with pytest.raises(CorpusDataError, match="is not a valid manifest"):
            load_manifest(path)


class TestVerify:
    def _written(self, tmp_path: Path) -> Manifest:
        (tmp_path / "markdown").mkdir()
        (tmp_path / "markdown" / "sd-50b-maint-battery-care-rev4.md").write_bytes(BODY)
        return Manifest.model_validate(manifest_data())

    def test_reports_nothing_when_every_file_matches(self, tmp_path: Path) -> None:
        assert verify_manifest(self._written(tmp_path), tmp_path) == []

    def test_reports_a_missing_file(self, tmp_path: Path) -> None:
        manifest = Manifest.model_validate(manifest_data())
        problems = verify_manifest(manifest, tmp_path)
        assert len(problems) == 1
        assert "missing file" in problems[0]

    def test_reports_an_edited_file(self, tmp_path: Path) -> None:
        """Catches a generated document edited by hand, which would otherwise
        survive until the next regeneration silently reverted it."""
        manifest = self._written(tmp_path)
        (tmp_path / "markdown" / "sd-50b-maint-battery-care-rev4.md").write_bytes(b"# Tampered\n")
        problems = verify_manifest(manifest, tmp_path)
        assert any("hashes to" in problem for problem in problems)

    def test_reports_every_problem_in_one_run(self, tmp_path: Path) -> None:
        """A fragment edit changes many files at once; reporting them one run at
        a time is how checking becomes tedious enough to skip."""
        second = entry_data(doc_id="other", path="markdown/other.md")
        manifest = Manifest.model_validate(manifest_data(total=2, documents=[entry_data(), second]))
        problems = verify_manifest(manifest, tmp_path)
        assert len(problems) == 2
