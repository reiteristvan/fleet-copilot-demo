"""Capture real AnalyzeResult payloads to use as test fixtures.

Run once, deliberately, against the deployed account:

    uv run python scripts/capture_layout_fixtures.py

The three documents span what the corpus can throw at the service: a PDF with a
text layer, a PDF with none, and a DOCX. Everything downstream is tested against
these rather than against the network.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeResult,
    DocumentContentFormat,
    StringIndexType,
)

from fleet_copilot.config import load_settings
from fleet_copilot.credentials import get_async_credential

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "corpus" / "published"
FIXTURES = ROOT / "tests" / "ingest" / "fixtures" / "layout"

DOCUMENTS = (
    "sdm-43-service-manual.pdf",
    "service-report-sd50b-2026-10142-17.pdf",
    "sdr-90-operator-manual-hu.docx",
)


async def main() -> None:
    settings = load_settings()
    endpoint = settings.azure_document_intelligence_endpoint
    if endpoint is None:
        raise SystemExit("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is not set")

    await asyncio.to_thread(FIXTURES.mkdir, parents=True, exist_ok=True)
    credential = get_async_credential()
    async with credential, DocumentIntelligenceClient(endpoint, credential) as client:
        for name in DOCUMENTS:
            data = await asyncio.to_thread((PUBLISHED / name).read_bytes)
            poller = await client.begin_analyze_document(
                "prebuilt-layout",
                body=data,
                output_content_format=DocumentContentFormat.MARKDOWN,
                # Not the SDK default of textElements, which counts grapheme
                # clusters. Python indexes strings by code point, and where the
                # two diverge every offset after the divergence is wrong.
                string_index_type=StringIndexType.UNICODE_CODE_POINT,
            )
            result: AnalyzeResult = await poller.result()
            target = FIXTURES / f"{Path(name).stem}.json"
            payload = json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n"
            await asyncio.to_thread(target.write_text, payload, encoding="utf-8")
            print(f"{target.name}: {len(result.content)} characters of markdown")


if __name__ == "__main__":
    asyncio.run(main())
