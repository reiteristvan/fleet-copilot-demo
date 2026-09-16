"""Upload the published corpus to Blob Storage.

Split so that everything with a decision in it -- which blob a document becomes,
what content type it carries, whether it needs uploading at all -- is a pure
function over the manifest and can be tested without an Azure account. Only
:func:`run` touches the network.

Authentication is :func:`fleet_copilot.credentials.get_credential` and nothing
else. ADR 0002 disables key-based auth at the resource level, so a connection
string here would not fail in review, it would fail at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot.config import Settings
from fleet_copilot.corpus.manifest import Manifest
from fleet_copilot.corpus.seed import CorpusDataError

CONTENT_TYPES: Final = {
    ".md": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
"""Content type per extension.

Set explicitly because the SDK's default is ``application/octet-stream``, and a
document ingested from a blob typed as an octet stream has to be sniffed rather
than dispatched on -- which is the kind of guess the ingest stage should not
have to make.
"""

CHECKSUM_METADATA_KEY: Final = "sha256"
"""Blob metadata key holding the manifest's content hash.

Blob metadata is sent as HTTP headers, so the key and value must be ASCII. A
hex digest and these key names are.
"""


class BlobUpload(BaseModel):
    """One file to upload, and where it goes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: Annotated[str, Field(min_length=1)]
    blob_name: Annotated[str, Field(min_length=1)]
    local_path: Path
    content_type: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: Annotated[int, Field(gt=0)]
    metadata: Mapping[str, str]


def content_type_for(suffix: str) -> str:
    """Return the content type for ``suffix``, or raise for one we do not publish."""
    try:
        return CONTENT_TYPES[suffix]
    except KeyError:
        msg = (
            f"no content type registered for {suffix!r}; "
            f"published formats are {sorted(CONTENT_TYPES)}"
        )
        raise CorpusDataError(msg) from None


def plan_uploads(manifest: Manifest, corpus_root: Path) -> tuple[BlobUpload, ...]:
    """Turn the manifest into the set of blobs to write.

    Blobs are named ``<doc_id><extension>`` at the container root rather than
    mirroring the ``markdown/`` and ``published/`` split on disk. That split is
    about reviewability -- keeping a diffable Markdown copy of a document
    published as a PDF -- and is meaningless to a consumer of the container,
    which should see one blob per document.
    """
    uploads: list[BlobUpload] = []
    for entry in manifest.documents:
        source = corpus_root / entry.path
        suffix = source.suffix
        uploads.append(
            BlobUpload(
                doc_id=entry.doc_id,
                blob_name=f"{entry.doc_id}{suffix}",
                local_path=source,
                content_type=content_type_for(suffix),
                sha256=entry.sha256,
                size=entry.bytes,
                metadata={
                    CHECKSUM_METADATA_KEY: entry.sha256,
                    "doc_id": entry.doc_id,
                    "type": entry.type.value,
                    "language": entry.language.value,
                    "format": entry.format.value,
                },
            )
        )

    names = [upload.blob_name for upload in uploads]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        msg = f"two documents would be written to the same blob: {duplicates}"
        raise CorpusDataError(msg)
    return tuple(uploads)


def select_changed(
    uploads: Sequence[BlobUpload], existing: Mapping[str, str]
) -> tuple[BlobUpload, ...]:
    """Return the uploads whose content differs from what is already in the container.

    ``existing`` maps blob name to the ``sha256`` metadata already recorded
    there. Comparing hashes rather than re-uploading everything keeps a
    regeneration that changed two documents from rewriting all 120 -- which
    matters less for cost than for the blob change feed, where 120 spurious
    writes look exactly like a real corpus refresh.
    """
    return tuple(upload for upload in uploads if existing.get(upload.blob_name) != upload.sha256)


def container_endpoint(settings: Settings) -> tuple[str, str]:
    """Return the blob endpoint and container name, or explain what is missing."""
    if not settings.azure_storage_blob_endpoint:
        msg = (
            "AZURE_STORAGE_BLOB_ENDPOINT is not set. Populate it from "
            "`./infra/deploy.sh dev`, which prints it as an export line."
        )
        raise CorpusDataError(msg)
    return settings.azure_storage_blob_endpoint, settings.azure_storage_container


class UploadReport(BaseModel):
    """What an upload run did, or would have done."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    container: str
    planned: int
    uploaded: int
    skipped: int
    dry_run: bool

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        verb = "would upload" if self.dry_run else "uploaded"
        return (
            f"{self.container}: {self.planned} documents, "
            f"{verb} {self.uploaded}, {self.skipped} already current"
        )


def run(
    manifest: Manifest,
    corpus_root: Path,
    settings: Settings,
    *,
    dry_run: bool = True,
) -> UploadReport:
    """Upload the corpus, or report what an upload would do.

    ``dry_run`` defaults to true because this is the one part of the generator
    that reaches outside the repository, and a container is shared state: the
    default should be the one that cannot surprise anybody.
    """
    # Imported here rather than at module scope so the planning functions above
    # stay importable without the Azure SDK. They are what the tests exercise,
    # and they are pure.
    from azure.storage.blob import BlobServiceClient, ContentSettings

    from fleet_copilot.credentials import get_credential

    endpoint, container_name = container_endpoint(settings)
    uploads = plan_uploads(manifest, corpus_root)

    client = BlobServiceClient(account_url=endpoint, credential=get_credential())
    container = client.get_container_client(container_name)

    existing: dict[str, str] = {}
    for blob in container.list_blobs(include=["metadata"]):
        metadata = blob.metadata or {}
        checksum = metadata.get(CHECKSUM_METADATA_KEY)
        if checksum:
            existing[blob.name] = checksum

    changed = select_changed(uploads, existing)
    if dry_run:
        return UploadReport(
            container=container_name,
            planned=len(uploads),
            uploaded=len(changed),
            skipped=len(uploads) - len(changed),
            dry_run=True,
        )

    for upload in changed:
        container.upload_blob(
            name=upload.blob_name,
            data=upload.local_path.read_bytes(),
            overwrite=True,
            content_settings=ContentSettings(content_type=upload.content_type),
            metadata=dict(upload.metadata),
        )

    return UploadReport(
        container=container_name,
        planned=len(uploads),
        uploaded=len(changed),
        skipped=len(uploads) - len(changed),
        dry_run=False,
    )
