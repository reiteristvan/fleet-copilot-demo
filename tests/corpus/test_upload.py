"""Upload planning, which is everything about uploading that is a decision.

:func:`fleet_copilot.corpus.upload.run` is a loop over the network and is not
tested here. Everything it decides -- blob names, content types, what is already
current -- is a pure function of the manifest and is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.config import Settings
from fleet_copilot.corpus.manifest import Manifest, sha256_of
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.corpus.upload import (
    CHECKSUM_METADATA_KEY,
    UploadReport,
    container_endpoint,
    content_type_for,
    plan_uploads,
    select_changed,
)

BODY = b"# Battery care\n"


def entry(doc_id: str, path: str, **overrides: Any) -> dict[str, Any]:
    """A manifest entry payload."""
    data: dict[str, Any] = {
        "doc_id": doc_id,
        "type": "maintenance_procedure",
        "language": "en",
        "format": "markdown",
        "path": path,
        "sha256": sha256_of(BODY + doc_id.encode()),
        "bytes": len(BODY),
    }
    return data | overrides


def manifest(*entries: dict[str, Any]) -> Manifest:
    """A manifest over ``entries``."""
    rows = list(entries) or [entry("a", "markdown/a.md")]
    return Manifest.model_validate({"seed": 1, "total": len(rows), "documents": rows})


class TestContentTypes:
    @pytest.mark.parametrize(
        ("suffix", "expected"),
        [
            (".md", "text/markdown; charset=utf-8"),
            (".pdf", "application/pdf"),
            (
                ".docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ],
    )
    def test_every_published_format_has_one(self, suffix: str, expected: str) -> None:
        """The SDK default is application/octet-stream, which forces ingest to
        sniff the format instead of dispatching on it."""
        assert content_type_for(suffix) == expected

    def test_an_unpublished_format_is_refused(self) -> None:
        with pytest.raises(CorpusDataError, match="no content type registered"):
            content_type_for(".xlsx")


class TestPlanUploads:
    def test_names_a_blob_after_its_document(self, tmp_path: Path) -> None:
        """One blob per document, flat. The markdown/published split on disk is
        about reviewability and means nothing to a consumer of the container."""
        uploads = plan_uploads(
            manifest(
                entry("glossary", "markdown/glossary.md"),
                entry(
                    "sdr-90-operator-manual-hu",
                    "published/sdr-90-operator-manual-hu.docx",
                    format="docx",
                    language="hu",
                ),
            ),
            tmp_path,
        )
        assert [u.blob_name for u in uploads] == [
            "glossary.md",
            "sdr-90-operator-manual-hu.docx",
        ]

    def test_resolves_the_local_path_under_the_corpus_root(self, tmp_path: Path) -> None:
        upload = plan_uploads(manifest(entry("glossary", "markdown/glossary.md")), tmp_path)[0]
        assert upload.local_path == tmp_path / "markdown" / "glossary.md"

    def test_carries_the_hash_and_the_front_matter_facts_as_metadata(self, tmp_path: Path) -> None:
        """So a consumer can tell what a blob is without parsing it, and so the
        next upload can tell whether it is current."""
        upload = plan_uploads(manifest(entry("glossary", "markdown/glossary.md")), tmp_path)[0]
        assert upload.metadata[CHECKSUM_METADATA_KEY] == upload.sha256
        assert upload.metadata["doc_id"] == "glossary"
        assert upload.metadata["type"] == "maintenance_procedure"
        assert upload.metadata["language"] == "en"

    def test_metadata_is_ascii(self, tmp_path: Path) -> None:
        """Blob metadata is sent as HTTP headers."""
        for upload in plan_uploads(manifest(entry("glossary", "markdown/glossary.md")), tmp_path):
            for key, value in upload.metadata.items():
                key.encode("ascii")
                value.encode("ascii")

    def test_refuses_two_documents_that_would_collide(self, tmp_path: Path) -> None:
        """One blob silently overwriting another would shrink the corpus in the
        container while the manifest still claimed the full count."""
        rows = [
            entry("same", "markdown/same.md"),
            entry("same", "published/same.md"),
        ]
        payload = {"seed": 1, "total": 2, "documents": rows}
        with pytest.raises(ValueError, match="doc_id more than once"):
            plan_uploads(Manifest.model_validate(payload), tmp_path)

    def test_plans_every_document_in_the_manifest(self, tmp_path: Path) -> None:
        rows = [entry(f"doc-{index}", f"markdown/doc-{index}.md") for index in range(5)]
        assert len(plan_uploads(manifest(*rows), tmp_path)) == 5


class TestSelectChanged:
    def test_uploads_everything_into_an_empty_container(self, tmp_path: Path) -> None:
        uploads = plan_uploads(manifest(entry("a", "markdown/a.md")), tmp_path)
        assert select_changed(uploads, {}) == uploads

    def test_skips_a_blob_whose_hash_already_matches(self, tmp_path: Path) -> None:
        uploads = plan_uploads(manifest(entry("a", "markdown/a.md")), tmp_path)
        existing = {uploads[0].blob_name: uploads[0].sha256}
        assert select_changed(uploads, existing) == ()

    def test_uploads_a_blob_whose_hash_differs(self, tmp_path: Path) -> None:
        uploads = plan_uploads(manifest(entry("a", "markdown/a.md")), tmp_path)
        assert select_changed(uploads, {uploads[0].blob_name: "0" * 64}) == uploads

    def test_uploads_a_blob_with_no_recorded_hash(self, tmp_path: Path) -> None:
        """A blob uploaded by something other than this script has no checksum
        metadata, and assuming it is current would leave it stale forever."""
        uploads = plan_uploads(manifest(entry("a", "markdown/a.md")), tmp_path)
        assert select_changed(uploads, {"unrelated.md": "0" * 64}) == uploads

    def test_only_the_changed_documents_are_selected(self, tmp_path: Path) -> None:
        """A regeneration that changed two documents must not rewrite all 120:
        118 spurious writes look exactly like a real refresh in the change feed."""
        rows = [entry(f"doc-{index}", f"markdown/doc-{index}.md") for index in range(5)]
        uploads = plan_uploads(manifest(*rows), tmp_path)
        existing = {u.blob_name: u.sha256 for u in uploads}
        existing[uploads[2].blob_name] = "0" * 64
        assert select_changed(uploads, existing) == (uploads[2],)


class TestSettings:
    def test_explains_a_missing_endpoint(self) -> None:
        settings = Settings(database_url="postgresql://x/y", azure_storage_blob_endpoint=None)
        with pytest.raises(CorpusDataError, match="AZURE_STORAGE_BLOB_ENDPOINT is not set"):
            container_endpoint(settings)

    def test_returns_the_endpoint_and_container(self) -> None:
        settings = Settings(
            database_url="postgresql://x/y",
            azure_storage_blob_endpoint="https://example.blob.core.windows.net/",
        )
        endpoint, container = container_endpoint(settings)
        assert endpoint == "https://example.blob.core.windows.net/"
        assert container == "raw-docs"


class TestUploadReport:
    def test_a_dry_run_says_it_would_upload(self) -> None:
        report = UploadReport(
            container="raw-docs", planned=120, uploaded=2, skipped=118, dry_run=True
        )
        assert "would upload 2" in report.describe()

    def test_a_real_run_says_it_uploaded(self) -> None:
        report = UploadReport(
            container="raw-docs", planned=120, uploaded=2, skipped=118, dry_run=False
        )
        assert "uploaded 2" in report.describe()
        assert "would" not in report.describe()

    def test_the_description_is_ascii(self) -> None:
        """It is printed to a console that may be cp1252."""
        report = UploadReport(container="raw-docs", planned=1, uploaded=1, skipped=0, dry_run=True)
        report.describe().encode("ascii")
