"""Parse the whole corpus, and never twice for the same bytes.

Split the way corpus/upload.py is split: the dispatch and the caching are pure
functions and small classes tested against a fake analyser, and only :func:`run`
reaches outside the repository.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from fleet_copilot.config import Settings
from fleet_copilot.corpus.manifest import Manifest
from fleet_copilot.corpus.models import OutputFormat
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.corpus.upload import content_type_for
from fleet_copilot.ingest.cache import BlobLayoutCache, LayoutCache, cache_key
from fleet_copilot.ingest.chunking import DocumentContext
from fleet_copilot.ingest.chunking.base import chunkers
from fleet_copilot.ingest.embedding.client import AzureEmbedder
from fleet_copilot.ingest.embedding.models import EmbedReport
from fleet_copilot.ingest.embedding.run import embed_chunks
from fleet_copilot.ingest.embedding.store import EmbeddingStore
from fleet_copilot.ingest.layout import MODEL_ID, AzureLayoutParser, layout_from_analyze_result
from fleet_copilot.ingest.markdown import MarkdownParser
from fleet_copilot.ingest.models import Chunk, StrategyId
from fleet_copilot.ingest.parse import ParsedDocument


class Analyser(Protocol):
    """Anything that can turn bytes into a raw AnalyzeResult payload."""

    async def analyse(self, data: bytes) -> Mapping[str, Any]: ...


class Target(BaseModel):
    """One document to parse, and what it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    path: str
    content_type: str


def _targets(manifest: Manifest, *, converted: bool) -> tuple[Target, ...]:
    return tuple(
        Target(
            doc_id=entry.doc_id,
            path=entry.path,
            content_type=content_type_for(Path(entry.path).suffix),
        )
        for entry in manifest.documents
        if (entry.format is not OutputFormat.MARKDOWN) is converted
    )


def analyse_targets(manifest: Manifest) -> tuple[Target, ...]:
    """The 25 documents that go to Document Intelligence."""
    return _targets(manifest, converted=True)


def native_targets(manifest: Manifest) -> tuple[Target, ...]:
    """The 95 Markdown documents, parsed locally and never sent anywhere.

    Their headings are already in the text. Sending them would pay per page to
    recover what is sitting in plain sight and would OCR prose we wrote.
    """
    return _targets(manifest, converted=False)


class CachedParser:
    """An analyser with a cache in front of it. Implements DocumentParser."""

    def __init__(self, analyser: Analyser, cache: LayoutCache, *, api_version: str) -> None:
        self._analyser = analyser
        self._cache = cache
        self._api_version = api_version

    def key_for(self, data: bytes) -> str:
        """The cache key these bytes would be stored under."""
        return cache_key(
            source_sha256=hashlib.sha256(data).hexdigest(),
            model_id=MODEL_ID,
            api_version=self._api_version,
        )

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Return the parsed document, analysing only on a cache miss.

        ``content_type`` is unused: the service sniffs the format itself. It is
        in the signature because DocumentParser requires it.
        """
        payload = await self._cache.get(self.key_for(data))
        if payload is None:
            payload = await self._analyser.analyse(data)
            await self._cache.put(self.key_for(data), payload)
        return layout_from_analyze_result(
            payload, doc_id=doc_id, source_sha256=hashlib.sha256(data).hexdigest()
        )


class ParseReport(BaseModel):
    """What a parse run did, or would have done."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    planned: int
    native: int
    analysed: int
    cached: int
    dry_run: bool

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        verb = "would analyse" if self.dry_run else "analysed"
        return (
            f"{self.planned} documents, {self.native} parsed natively, "
            f"{verb} {self.analysed}, {self.cached} already cached"
        )


def endpoints(settings: Settings) -> tuple[str, str, str]:
    """Return the DI endpoint, the blob endpoint and the cache container.

    Or name the variables that are missing, in the wording corpus/upload.py uses
    for the same failure.
    """
    missing = [
        name
        for name, value in (
            ("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", settings.azure_document_intelligence_endpoint),
            ("AZURE_STORAGE_BLOB_ENDPOINT", settings.azure_storage_blob_endpoint),
        )
        if not value
    ]
    if missing:
        msg = (
            f"{' and '.join(missing)} is not set. Populate it from "
            "`./infra/deploy.sh dev`, which prints it as an export line."
        )
        raise CorpusDataError(msg)
    return (
        str(settings.azure_document_intelligence_endpoint),
        str(settings.azure_storage_blob_endpoint),
        settings.azure_layout_cache_container,
    )


async def run(
    manifest: Manifest,
    corpus_root: Path,
    settings: Settings,
    *,
    dry_run: bool = True,
) -> ParseReport:
    """Parse every document, or report what a parse would do.

    ``dry_run`` defaults to true because this is the part that reaches outside
    the repository and spends money; the default should be the one that cannot
    surprise anybody.
    """
    di_endpoint, blob_endpoint, container = endpoints(settings)
    api_version = settings.azure_document_intelligence_api_version
    cache = BlobLayoutCache(blob_endpoint, container)
    parser = CachedParser(
        AzureLayoutParser(di_endpoint, api_version=api_version), cache, api_version=api_version
    )

    native = MarkdownParser()
    native_count = 0
    for target in native_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        if not dry_run:
            await native.parse(data, doc_id=target.doc_id, content_type=target.content_type)
        native_count += 1

    analysed = 0
    cached = 0
    for target in analyse_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        if await cache.get(parser.key_for(data)) is not None:
            cached += 1
            continue
        if not dry_run:
            await parser.parse(data, doc_id=target.doc_id, content_type=target.content_type)
        analysed += 1

    return ParseReport(
        planned=manifest.total,
        native=native_count,
        analysed=analysed,
        cached=cached,
        dry_run=dry_run,
    )


async def chunk_markdown_corpus(
    manifest: Manifest, corpus_root: Path
) -> dict[StrategyId, list[Chunk]]:
    """Chunk every Markdown document under all three strategies.

    Needs no Azure account, which is why it is what the test suite exercises:
    the 95 Markdown documents are 79% of the corpus and cover both languages,
    every document type and every structure the chunkers care about.
    """
    parser = MarkdownParser()
    strategies = chunkers()
    by_strategy: dict[StrategyId, list[Chunk]] = {key: [] for key in strategies}

    entries = {entry.doc_id: entry for entry in manifest.documents}
    for target in native_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        document = await parser.parse(data, doc_id=target.doc_id, content_type=target.content_type)
        context = DocumentContext.from_entry(entries[target.doc_id])
        for key, chunker in strategies.items():
            by_strategy[key].extend(chunker.chunk(document, context))

    return by_strategy


async def chunk_corpus(
    manifest: Manifest, corpus_root: Path, settings: Settings
) -> dict[StrategyId, list[Chunk]]:
    """Chunk all 120 documents. The converted 25 come from the layout cache.

    Raises if a converted document is not cached rather than analysing it:
    chunking is not the place to spend money, and an uncached document means
    `just corpus-parse --apply` has not been run.
    """
    by_strategy = await chunk_markdown_corpus(manifest, corpus_root)

    di_endpoint, blob_endpoint, container = endpoints(settings)
    api_version = settings.azure_document_intelligence_api_version
    cache = BlobLayoutCache(blob_endpoint, container)
    parser = CachedParser(
        AzureLayoutParser(di_endpoint, api_version=api_version), cache, api_version=api_version
    )
    strategies = chunkers()
    entries = {entry.doc_id: entry for entry in manifest.documents}

    for target in analyse_targets(manifest):
        data = await asyncio.to_thread((corpus_root / target.path).read_bytes)
        key = parser.key_for(data)
        payload = await cache.get(key)
        if payload is None:
            msg = f"{target.doc_id} is not in the layout cache; run `just corpus-parse --apply`"
            raise CorpusDataError(msg)
        document = layout_from_analyze_result(
            payload,
            doc_id=target.doc_id,
            source_sha256=hashlib.sha256(data).hexdigest(),
        )
        context = DocumentContext.from_entry(entries[target.doc_id])
        for strategy_id, chunker in strategies.items():
            by_strategy[strategy_id].extend(chunker.chunk(document, context))

    return by_strategy


async def embed_corpus(
    manifest: Manifest,
    corpus_root: Path,
    settings: Settings,
    *,
    dry_run: bool = True,
) -> dict[StrategyId, EmbedReport]:
    """Chunk the corpus and embed every strategy's chunks, cache-first.

    One store and one embedder across all three strategies: they share the cache
    by design, so embedding all three costs the union of their content hashes
    rather than three separate runs.
    """
    by_strategy = await chunk_corpus(manifest, corpus_root, settings)
    store = EmbeddingStore(settings.database_url)
    embedder = AzureEmbedder(settings)

    reports: dict[StrategyId, EmbedReport] = {}
    for strategy, chunks in by_strategy.items():
        reports[strategy] = await embed_chunks(
            chunks,
            store,
            embedder,
            dimensions=settings.azure_openai_embedding_dimensions,
            budget_tokens=settings.embedding_batch_tokens,
            dry_run=dry_run,
        )
    return reports
