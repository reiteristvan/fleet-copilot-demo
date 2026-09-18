# Parsing and the Layout Cache — Implementation Plan (1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ingest` a parser that turns all 120 corpus documents into one Markdown string with role-tagged spans over it — Markdown natively, PDF and DOCX through Document Intelligence — backed by a content-addressed cache, and carry the four metadata keys that rendering strips through the manifest instead.

**Architecture:** A `DocumentParser` Protocol with three implementations. `MarkdownParser` reads the 95 Markdown documents natively; `AzureLayoutParser` sends the 25 converted files to `prebuilt-layout` with Markdown output; `LocalParser` reads a PDF text layer offline and raises rather than degrading on the five image-only files. A `LayoutCache` Protocol with a blob-backed and a directory-backed implementation stores the *raw* `AnalyzeResult` JSON keyed by content hash, so re-interpreting a layout is free and re-analysing is the only thing that costs. Every module holding a decision is pure and importable without the Azure SDK; only the `run`-shaped functions touch the network.

**Tech Stack:** Python 3.12, pydantic v2 (frozen, `extra="forbid"`), `azure-ai-documentintelligence` 1.0.x (aio client), `azure-identity` (aio credential), `pymupdf`, `python-docx`, `azure-storage-blob`, Bicep, pytest + pytest-asyncio (strict mode), mypy --strict, ruff.

**Spec:** `docs/adr/0005-document-parsing-and-the-layout-cache.md` and `docs/adr/0006-the-chunk-contract.md`

**Scope boundary.** This plan builds *what the chunkers read*. It writes no chunker, does not touch `Chunk`, and makes no embedding call.

- **Plan 2 — chunking:** the ten-field `Chunk` contract, the three strategies, table chunks, unsplit step lists, `scripts/chunk_stats.py`, `docs/chunking.md`.
- **Plan 3 — embeddings:** `text-embedding-3-large`, batching, async, 429 backoff, the Postgres cache keyed by `content_hash`, and the "zero embedding calls on a clean re-run" criterion.

## Global Constraints

From `CLAUDE.md` and the two ADRs. Every task's requirements implicitly include this section.

- **`just check` is the gate.** Paste its output; never assert it passed.
- **Never weaken a gate to make a change land.** No new ruff ignores, no relaxed mypy settings, no `xfail` to get green. If a gate is genuinely wrong, say so and stop.
- **Every `noqa` and `type: ignore` carries a code and a reason.** A bare `# type: ignore` will not pass review.
- **Do not touch without an explicit human go-ahead:** the `[tool.*]` sections of `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/`, `uv.lock` by hand, `CLAUDE.md`. Adding entries to `[dependency-groups].dev` is *not* a `[tool.*]` change and is in scope; relock with `uv`, never by hand.
- **Python 3.12 floor.** `X | None`, built-in generics, `Self`, `datetime.UTC`, `StrEnum`.
- **`mypy --strict` covers `src` and `tests`.** Every function, including every test function, is annotated — `-> None` on tests.
- **Domain models are pydantic v2, frozen, `extra="forbid"`.** Each validator's docstring says which failure it prevents, not what the code does.
- **Nothing blocking runs in a coroutine.** `pathlib`, `requests`, `time.sleep` go through `asyncio.to_thread` or an async client. Ruff's `ASYNC` rules enforce this.
- **Async tests opt in** with `@pytest.mark.asyncio` (`asyncio_mode = "strict"`).
- **Tests mirror the source layout.** `src/fleet_copilot/ingest/x.py` → `tests/ingest/test_x.py`. New behaviour ships with its tests in the same commit.
- **Test invalid input through `model_validate`,** not with a cast or an ignore on the constructor.
- **Comment only what the code cannot say itself** — a constraint, a rejected alternative, a non-obvious failure prevented, an external fact.
- **Never introduce key-based auth to an Azure data plane.** Use `get_credential()` / `get_async_credential()`. `tests/test_no_key_based_auth.py` enforces this.
- **Pin Azure API versions to values already checked:** ARM `2026-03-01` for `Microsoft.CognitiveServices/accounts`, `2025-08-01` for storage, `2024-08-01` for budgets, `2022-04-01` for role assignments; data-plane `2024-11-30` (the SDK default, v4.0 GA).
- **Commits are atomic and semantic.** One concern per commit; the body says *why*.
- **Subscription:** `00000000-0000-0000-0000-000000000000` ("Azure subscription 1"), tenant `00000000-0000-0000-0000-000000000000`, region `swedencentral`, resource group `rg-fleet-copilot-dev`.
- **Role definition id for Cognitive Services User:** `a97b65f3-24c7-4388-baec-2e87135dc908`.
- **Spans are `unicodeCodePoint`**, never the SDK's `textElements` default, and `ParsedBlock.text` is always `content[start:end]` — derived, never copied from `paragraph.content`.
- **`MANIFEST_VERSION` goes to 2** in this plan. Document bytes and their SHA-256s do not change; only `data/manifest.json` gains four keys.

## File Structure

| File | Responsibility |
| --- | --- |
| `infra/modules/docintel.bicep` (new) | The Document Intelligence account. Nothing else. |
| `infra/modules/storage.bicep` (modify) | Gains the `layout-cache` container beside `raw-docs`. |
| `infra/modules/rbac.bicep` (modify) | Gains the Cognitive Services User assignment. |
| `infra/main.bicep` (modify) | Wires the module in; outputs the endpoint and cache container. |
| `infra/budget.bicep` (modify) | Gains the second, resource-filtered $15 budget. |
| `infra/deploy.sh` (modify) | Prints the two new environment variables. |
| `src/fleet_copilot/corpus/manifest.py` (modify) | `ManifestEntry` gains four keys; `MANIFEST_VERSION` → 2. |
| `src/fleet_copilot/corpus/writer.py` (modify) | Populates them from the front matter. |
| `src/fleet_copilot/corpus/upload.py` (modify) | Writes them as blob metadata. |
| `src/fleet_copilot/ingest/parse.py` (new) | The contract: `BlockRole`, `ParsedBlock`, `ParsedPage`, `ParsedDocument`, `DocumentParser`, `ParseError`. Pure; no SDK import. |
| `src/fleet_copilot/ingest/layout.py` (new) | `layout_from_analyze_result()` (pure) and `AzureLayoutParser` (network, lazy import). |
| `src/fleet_copilot/ingest/markdown.py` (new) | `MarkdownParser`. Pure, no third-party dependency. |
| `src/fleet_copilot/ingest/fallback.py` (new) | `LocalParser`. Raises on anything it cannot honestly read. |
| `src/fleet_copilot/ingest/cache.py` (new) | `cache_key()`, `LayoutCache`, `LocalLayoutCache`, `BlobLayoutCache`. |
| `src/fleet_copilot/ingest/run.py` (new) | Dispatch, `CachedParser`, `ParseReport`, `run()`. |
| `src/fleet_copilot/credentials.py` (modify) | `get_async_credential()`. |
| `src/fleet_copilot/config.py` (modify) | Three new settings. |
| `scripts/parse_corpus.py` (new) | Argument-parsing shim only. |
| `scripts/capture_layout_fixtures.py` (new) | One-shot fixture capture, committed so it is repeatable. |
| `tests/ingest/fixtures/layout/*.json` (new) | Three real `AnalyzeResult` payloads, captured once. |

Task order is dependency order. Only Task 2 spends money; Tasks 3–9 need no Azure account.

**A deliberate non-decision:** `BlockRole` has no `LIST_ITEM`. Document Intelligence has no list role, so a Markdown parser that emitted one would produce output the Azure parser could not match, and a chunker would then behave differently depending on which parser ran. ADR 0006 requires step lists to be atomic; the chunker gets that by reading Markdown ordered-list syntax out of `content`, which both parsers produce identically.

---

### Task 1: Infrastructure — the account, the container, the role, the budget

No Python. Ends when `--validate` and `--what-if` both succeed, which proves the template without creating anything.

**Files:**
- Create: `infra/modules/docintel.bicep`
- Modify: `infra/modules/storage.bicep`, `infra/modules/rbac.bicep`, `infra/main.bicep`, `infra/budget.bicep`, `infra/deploy.sh:139-172`, `infra/README.md`
- Test: `./infra/deploy.sh dev --validate` and `./infra/deploy.sh dev --what-if`

**Interfaces:**
- Consumes: nothing.
- Produces: deployment outputs `documentIntelligenceEndpoint` (string, e.g. `https://di-fleet-copilot-dev-<suffix>.cognitiveservices.azure.com/`) and `storageLayoutCacheContainer` (string, `layout-cache`). Task 2 reads both.

- [ ] **Step 1: Create `infra/modules/docintel.bicep`**

```bicep
@description('Globally unique Document Intelligence account name. Also used as the custom subdomain.')
param name string

@description('Azure region. West Europe and Sweden Central both carry the v4.0 GA API.')
param location string

param tags object = {}

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: name
  location: location
  tags: tags
  // The product was renamed to Azure AI Document Intelligence; the ARM kind was
  // not. 'DocumentIntelligence' is rejected as an unknown kind.
  kind: 'FormRecognizer'
  sku: {
    // Not F0. The free tier returns only the first two pages of any request and
    // rejects files over 4 MB, so a document parses without error and is
    // silently truncated. S0 has no monthly fee; it bills per page analysed.
    name: 'S0'
  }
  properties: {
    // Token auth only works against a custom subdomain, not the regional endpoint.
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.endpoint
```

- [ ] **Step 2: Add the cache container to `infra/modules/storage.bicep`**

Add the parameter beside the existing `containerName` (after line 10):

```bicep
@description('Container holding cached Document Intelligence layout JSON.')
param layoutCacheContainerName string = 'layout-cache'
```

Add the resource after the existing `rawDocs` resource:

```bicep
// A second container rather than a prefix inside raw-docs: the corpus upload
// lists raw-docs and compares every blob's sha256 metadata, and cache entries
// carry no such metadata. They would read as corpus documents that had drifted.
resource layoutCache 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-08-01' = {
  parent: blobService
  name: layoutCacheContainerName
  properties: {
    publicAccess: 'None'
  }
}
```

Add the output at the end of the file:

```bicep
output layoutCacheContainerName string = layoutCache.name
```

- [ ] **Step 3: Add the role assignment to `infra/modules/rbac.bicep`**

Add the parameter beside the other four names:

```bicep
param documentIntelligenceAccountName string
```

Add the role definition id beside the other four vars:

```bicep
// Cognitive Services User, not Cognitive Services OpenAI User: the
// OpenAI-specific role carries no Document Intelligence data actions.
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
```

Add the existing-resource reference and the assignment:

```bicep
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: documentIntelligenceAccountName
}

resource documentIntelligenceUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: documentIntelligence
  name: guid(documentIntelligence.id, principalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      cognitiveServicesUser
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
```

- [ ] **Step 4: Wire the module into `infra/main.bicep`**

Add the module after the `search` module:

```bicep
module documentIntelligence 'modules/docintel.bicep' = {
  scope: rg
  name: 'docintel'
  params: {
    name: 'di-${workload}-${environmentName}-${suffix}'
    location: location
    tags: tags
  }
}
```

Add the parameter to the existing `rbac` module block:

```bicep
    documentIntelligenceAccountName: documentIntelligence.outputs.name
```

Add two outputs at the end of the file:

```bicep
output documentIntelligenceEndpoint string = documentIntelligence.outputs.endpoint
output storageLayoutCacheContainer string = storage.outputs.layoutCacheContainerName
```

- [ ] **Step 5: Add the resource-scoped budget to `infra/budget.bicep`**

Add two parameters:

```bicep
@description('Environment slug, used to derive the resource group the filter points at.')
@allowed([
  'dev'
  'ci'
])
param environmentName string

@description('Document Intelligence account name, as deployed by main.bicep.')
param documentIntelligenceAccountName string
```

Add the resource id variable and the second budget:

```bicep
// Budget filters match on a lowercased resource id. Building it by hand rather
// than taking an output keeps this template deployable on its own, which is the
// whole reason it is not part of main.bicep.
var documentIntelligenceResourceId = toLower(
  '/subscriptions/${subscription().subscriptionId}/resourceGroups/rg-fleet-copilot-${environmentName}/providers/Microsoft.CognitiveServices/accounts/${documentIntelligenceAccountName}'
)

resource documentIntelligenceBudget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${name}-docintel'
  properties: {
    category: 'Cost'
    // Deliberately far below the subscription cap. Layout analysis bills per
    // page, and a runaway loop over the corpus is cheap enough to go unnoticed
    // against a $40 ceiling; this is the alert that would actually fire.
    amount: 15
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    filter: {
      dimensions: {
        name: 'ResourceId'
        operator: 'In'
        values: [
          documentIntelligenceResourceId
        ]
      }
    }
    notifications: {
      actualEighty: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      forecastedFull: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: contactEmails
      }
    }
  }
}

output documentIntelligenceBudgetId string = documentIntelligenceBudget.id
```

- [ ] **Step 6: Print the new outputs from `infra/deploy.sh`**

In `print_app_env`, add to `env_names` immediately after `AZURE_STORAGE_CONTAINER`:

```bash
    AZURE_LAYOUT_CACHE_CONTAINER
```

and immediately after `AZURE_KEY_VAULT_URI`:

```bash
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
```

Add the matching lines to the `--query` array **in the same order** — the function dies if the counts disagree, but it cannot detect a mismatched order, and a silently swapped pair would export an endpoint as a container name:

```
      properties.outputs.storageLayoutCacheContainer.value,
```
immediately after `storageContainerName.value`, and

```
      properties.outputs.documentIntelligenceEndpoint.value,
```
immediately after `keyVaultUri.value`.

- [ ] **Step 7: Validate the template**

Run: `./infra/deploy.sh dev --validate`
Expected: `template is valid`

If it fails on the budget filter, the error names the offending property path — budgets reject an unknown dimension name outright, so a typo in `ResourceId` surfaces here rather than at runtime.

- [ ] **Step 8: Preview the change against the live stack**

Run: `./infra/deploy.sh dev --what-if`

Expected, against a stack that already has the rest deployed:

```
  + Microsoft.CognitiveServices/accounts/di-fleet-copilot-dev-<suffix>
  + Microsoft.Storage/.../blobServices/default/containers/layout-cache
Resource changes: 2 to create, 10 to modify, 4 no change, 5 unsupported, 1 to ignore.
```

Two things about that line are counter-intuitive and neither is a problem.

**The role assignment is not one of the creates.** It appears in the *unsupported* count, which rises from 4 to 5. What-if cannot compute a role assignment's resource id ahead of time because the `guid()` depends on a `reference()` to the identity's principal id, so it declines to analyse it and says so in the diagnostics. The four existing assignments are already in the baseline 4.

**The 10 modifies are pre-existing noise, not yours.** What-if reports read-only and defaulted properties that the deployed resource has and the template does not declare — `properties.endpoint` on Search, `deleteRetentionPolicy` on blob services, `defaultEncryptionScope` on the `raw-docs` container. Do not try to silence them.

Verify that rather than trusting it. Stash the change and run the same command against the unmodified template:

```bash
git stash push -- infra/ && ./infra/deploy.sh dev --what-if | grep "^Resource changes:"
git stash pop
```

The baseline should read `10 to modify, 4 no change, 4 unsupported, 1 to ignore` — same modifies, two fewer creates, one fewer unsupported. **If the modify count rises above the baseline, that is yours and worth stopping for.**

> **`git stash` will corrupt `deploy.sh` on Windows.** The file contains one intentional bare CR — `tr -d '<CR>'`, which strips the carriage return `az -o tsv` adds — and the stash round-trip normalises it to LF, turning the command into `tr -d '<LF>'`. That collapses all eleven output values onto one line and trips the count check in `print_app_env`. After any stash round-trip, check it:
>
> ```bash
> python -c "d=open('infra/deploy.sh','rb').read(); print('bare CR:', d.count(b'\r')-d.count(b'\r\n'))"
> ```
>
> It must print `1`. If it prints `0`, run `git checkout HEAD -- infra/deploy.sh` and re-apply Step 6 with `write_bytes`, not `write_text` — `pathlib.write_text` translates `\n` to `\r\n` on Windows and is how the CR gets destroyed.

- [ ] **Step 9: Document it in `infra/README.md`**

Add a row to the "What gets deployed" table (`infra/README.md:11-20`), after the `search.bicep` row:

```markdown
| `docintel.bicep` | Azure AI Document Intelligence, S0, for `prebuilt-layout` |
```

Change the `storage.bicep` row to name both containers:

```markdown
| `storage.bicep` | Storage account with a `raw-docs` and a `layout-cache` container |
```

Extend the teardown note at line 118 to name the second budget too:

```console
$ az consumption budget delete --budget-name budget-fleet-copilot
$ az consumption budget delete --budget-name budget-fleet-copilot-docintel
```

Both are subscription-scoped and survive `az group delete`; the second would otherwise be left pointing at a resource id that no longer exists.

- [ ] **Step 10: Commit**

```bash
git add infra/
git commit -m "feat(infra): add Document Intelligence, its cache container and a budget

S0 rather than F0: the free tier truncates every request to two pages and
rejects files over 4 MB, which parses without error and silently loses
content. The ARM kind stays FormRecognizer because only the product was
renamed.

The cache gets its own container rather than a prefix in raw-docs, whose
listing is compared against manifest hashes -- cache entries carry no such
metadata and would read as corpus documents that had drifted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Deploy, add the dependencies, and capture the three fixtures

The only task that spends money. Expected total well under $1 — three single-file analyses at $10/1,000 pages.

**Depends on Task 3** for `get_async_credential`. Do Steps 1–4, then Task 3, then return for Steps 5–10.

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups].dev` only), `uv.lock` (via `uv`), `.env.example`, `src/fleet_copilot/config.py`
- Create: `scripts/capture_layout_fixtures.py`, `tests/ingest/fixtures/layout/*.json`
- Test: `tests/ingest/test_fixtures.py`

**Interfaces:**
- Consumes: Task 1's `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`; Task 3's `get_async_credential`.
- Produces: `Settings.azure_document_intelligence_endpoint: str | None`, `Settings.azure_document_intelligence_api_version: str = "2024-11-30"`, `Settings.azure_layout_cache_container: str = "layout-cache"`, and three fixture files at `tests/ingest/fixtures/layout/<stem>.json`, each the `as_dict()` of a real `AnalyzeResult`. Tasks 6 and 10 read these.

- [ ] **Step 1: Add the dependencies**

```bash
uv add --dev "azure-ai-documentintelligence>=1.0.2" "pymupdf>=1.24"
```

`uv add --dev` edits `[dependency-groups].dev` and relocks in one step. Do not hand-edit `uv.lock`. Both are dev-only: parsing is an offline operation, the API image installs with `--no-dev` and only ever reads the cache, and this keeps PyMuPDF's AGPL-3.0 licence out of an MIT wheel. `python-docx` and `azure-storage-blob` are already in the group.

- [ ] **Step 2: Add the three settings**

In `src/fleet_copilot/config.py`, after `azure_storage_container`:

```python
    azure_layout_cache_container: str = "layout-cache"

    azure_document_intelligence_endpoint: str | None = None

    # v4.0 GA, and the SDK's own default. Restated here because the cached
    # layout JSON is keyed by it: a version bump must invalidate the cache
    # rather than be absorbed silently.
    azure_document_intelligence_api_version: str = "2024-11-30"
```

and in `.env.example`, under the existing Azure section:

```
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=
AZURE_LAYOUT_CACHE_CONTAINER=layout-cache
```

- [ ] **Step 3: Deploy**

```bash
./infra/deploy.sh dev
```

Expected: the export block now carries `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and `AZURE_LAYOUT_CACHE_CONTAINER`. Copy both into `.env`.

- [ ] **Step 4: Confirm the account is what the template asked for**

```bash
az cognitiveservices account show \
  --name "$(az cognitiveservices account list -g rg-fleet-copilot-dev \
            --query "[?kind=='FormRecognizer'].name | [0]" -o tsv)" \
  -g rg-fleet-copilot-dev \
  --query "{endpoint:properties.endpoint, localAuthDisabled:properties.disableLocalAuth, sku:sku.name}"
```

Expected: an endpoint ending `.cognitiveservices.azure.com/`, `"localAuthDisabled": true`, `"sku": "S0"`.

Role assignments take a few minutes to propagate. If Step 6 returns 403, wait and retry — never add a key.

- [ ] **Step 5: Write `scripts/capture_layout_fixtures.py`**

```python
"""Capture real AnalyzeResult payloads to use as test fixtures.

Run once, deliberately, against the deployed account:

    uv run python scripts/capture_layout_fixtures.py

The three documents span what the corpus can throw at the service: a PDF with a
text layer, a PDF with none, and a DOCX. Everything downstream is tested against
these rather than against the network.
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
                string_index_type=StringIndexType.UNICODE_CODE_POINT,
            )
            result: AnalyzeResult = await poller.result()
            target = FIXTURES / f"{Path(name).stem}.json"
            payload = json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n"
            await asyncio.to_thread(target.write_text, payload, encoding="utf-8")
            print(f"{target.name}: {len(result.content)} characters of markdown")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Capture the fixtures**

```bash
uv run python scripts/capture_layout_fixtures.py
```

Expected: three lines, each with a non-zero character count. The scanned PDF (`service-report-sd50b-2026-10142-17`) reporting a non-zero count is the proof that OCR ran — that file has no text layer at all.

If any count is zero, stop. A zero-length result means the request succeeded and returned nothing, which is the failure mode F0 produces and S0 should not.

- [ ] **Step 7: Write the fixture guard test**

Create `tests/ingest/test_fixtures.py`:

```python
"""Assert the committed fixtures are what the rest of the ingest tests assume.

These three files stand in for the Document Intelligence service everywhere else
in the suite. If one is truncated, re-captured against a different API version,
or committed empty, every test reading it would go on passing against a weaker
document than it was written for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "layout"

STEMS = (
    "sdm-43-service-manual",
    "service-report-sd50b-2026-10142-17",
    "sdr-90-operator-manual-hu",
)


@pytest.mark.parametrize("stem", STEMS)
def test_fixture_is_a_usable_analyze_result(stem: str) -> None:
    payload = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))

    assert payload["apiVersion"] == "2024-11-30"
    assert payload["modelId"] == "prebuilt-layout"
    assert payload["content"], "empty content means the analyse call returned nothing"
    assert payload["paragraphs"], "no paragraphs means no structure to chunk on"


def test_the_service_manual_fixture_has_a_table() -> None:
    """ADR 0006 makes every table its own chunk, so the mapper must see one.

    The SDM-43 manual has a service-interval table. If prebuilt-layout returns
    no tables for it, Task 6 has nothing to map and Plan 2 has nothing to chunk.
    """
    payload = json.loads((FIXTURES / "sdm-43-service-manual.json").read_text(encoding="utf-8"))

    assert payload.get("tables"), "no tables in a document that has one"


def test_the_scanned_fixture_proves_ocr_ran() -> None:
    """The scanned PDF has no text layer, so any content at all came from OCR.

    This is the fixture that makes the Azure path worth paying for. If it comes
    back empty, the local fallback and the service are indistinguishable.
    """
    payload = json.loads(
        (FIXTURES / "service-report-sd50b-2026-10142-17.json").read_text(encoding="utf-8")
    )

    assert len(payload["content"]) > 200
```

- [ ] **Step 8: Run the fixture tests**

Run: `uv run pytest tests/ingest/test_fixtures.py -v`
Expected: 5 passed.

- [ ] **Step 9: Check the fixture sizes before committing**

```bash
ls -la tests/ingest/fixtures/layout/
```

The pre-commit `check for added large files` hook rejects anything over its threshold. OCR emits a word entry with a polygon per word, so the scanned fixture is the large one. If it trips the hook, do **not** raise the hook's limit — that is weakening a gate. Store that one gzipped and decompress it in the `fixture()` helper instead, with a comment saying why.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock .env.example src/fleet_copilot/config.py \
        scripts/capture_layout_fixtures.py tests/ingest/fixtures tests/ingest/test_fixtures.py
git commit -m "build: add the parsing dependencies and capture real layout fixtures

Both dev-only. Parsing is offline: the API image installs with --no-dev and
reads the cache, never the service. It also keeps PyMuPDF's AGPL-3.0 licence
out of an MIT wheel.

The three fixtures span what the corpus can throw at prebuilt-layout -- a PDF
with a text layer, a PDF with none, and a DOCX -- so every mapper test runs
against real service output without an Azure account or a bill.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The async credential

**Files:**
- Modify: `src/fleet_copilot/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `get_async_credential() -> azure.identity.aio.DefaultAzureCredential`. Tasks 2 and 10 use it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_credentials.py`:

```python
def test_async_credential_is_the_async_default_chain() -> None:
    """The aio SDK clients do not reject a synchronous credential at construction.

    They accept it and fail on the first request, inside a poller, where the
    traceback points at the SDK rather than at the credential that was wrong.
    """
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

    from fleet_copilot.credentials import get_async_credential

    assert isinstance(get_async_credential(), AsyncDefaultAzureCredential)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_credentials.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_async_credential'`

- [ ] **Step 3: Implement**

Add the import at the top of `src/fleet_copilot/credentials.py`:

```python
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
```

Append the function:

```python
def get_async_credential() -> AsyncDefaultAzureCredential:
    """Return the same chain as :func:`get_credential`, for the aio clients.

    A second function rather than a branch: the two are different types with
    different close semantics, and an aio client handed the synchronous one
    accepts it and fails on the first request instead of at construction. The
    caller closes this one -- ``async with credential:`` -- because its
    transport holds a connection pool.
    """
    return AsyncDefaultAzureCredential()
```

- [ ] **Step 4: Add the async transport**

The test will still fail, with `ImportError: aiohttp package is not installed`. `azure.identity.aio` needs an async HTTP transport and `azure-identity` does not pull one in. The module *imports* fine without it — the failure is at construction — which is why nothing catches this until the credential is actually built.

```bash
uv add "azure-core[aio]>=1.30"
```

A **main** dependency, not a dev one. The parsing SDKs are dev-only because parsing is offline, but `get_async_credential()` lives in `src/fleet_copilot/credentials.py` and is part of the package's surface; a function that a consumer can import should not need a dev extra to work. It costs `aiohttp` and six small transitive packages in the API image.

- [ ] **Step 5: Run it and watch it pass**

Run: `uv run pytest tests/test_credentials.py -v && uv run mypy`
Expected: 4 passed, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/credentials.py tests/test_credentials.py pyproject.toml uv.lock
git commit -m "feat(credentials): add the async credential the aio clients need

A separate function rather than a branch on environment. An aio client handed
the synchronous credential accepts it and fails on the first request, inside a
poller, where the traceback points at the SDK rather than at the credential.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Manifest v2 — carry the metadata that rendering strips

ADR 0006. A chunk must carry `machine_types`, `item_numbers`, `revision` and `effective_date`, and none of them survive into the PDF the parser reads.

**Files:**
- Modify: `src/fleet_copilot/corpus/manifest.py:24-76`, `src/fleet_copilot/corpus/writer.py:84-99`, `src/fleet_copilot/corpus/upload.py:83-99`
- Test: `tests/corpus/test_manifest.py`, `tests/corpus/test_upload.py`
- Regenerate: `data/manifest.json`

**Interfaces:**
- Consumes: `FrontMatter` from `corpus/document.py`.
- Produces: `ManifestEntry.machine_types: tuple[str, ...]`, `.item_numbers: tuple[str, ...]`, `.revision: int`, `.effective_date: date`; `MANIFEST_VERSION = 2`; blob metadata keys `machine_types`, `item_numbers`, `revision`, `effective_date`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/corpus/test_manifest.py` (add `from datetime import date` and `MANIFEST_VERSION` to its imports):

```python
def test_an_entry_carries_the_metadata_rendering_strips() -> None:
    """Front matter does not survive into a PDF, so the manifest carries it.

    Decompressing a published PDF's text streams finds the prose and none of the
    YAML keys, so a chunk built from one cannot recover its own machine types or
    effective date from what was parsed (ADR 0006).
    """
    entry = ManifestEntry.model_validate(
        {
            "doc_id": "sdm-43-service-manual",
            "type": "service_manual",
            "language": "en",
            "format": "pdf",
            "path": "published/sdm-43-service-manual.pdf",
            "sha256": "0" * 64,
            "bytes": 2639,
            "machine_types": ["SDM-43"],
            "item_numbers": ["1.291-101.0"],
            "revision": 3,
            "effective_date": "2026-03-09",
        }
    )

    assert entry.machine_types == ("SDM-43",)
    assert entry.item_numbers == ("1.291-101.0",)
    assert entry.revision == 3
    assert entry.effective_date == date(2026, 3, 9)


def test_the_manifest_version_is_two() -> None:
    """Bumped with the four new keys so a reader can tell an old manifest from a
    corrupt one -- which is the only reason the field exists."""
    assert MANIFEST_VERSION == 2
```

Append to `tests/corpus/test_upload.py`:

```python
def test_blob_metadata_carries_the_four_stripped_keys() -> None:
    """An ingest reading only the container must reach the same metadata.

    Reading it from the local Markdown copy instead would work on a laptop and
    fail wherever those copies are not present, which is everywhere else.
    """
    manifest = a_manifest_with_one_pdf_entry()

    upload = plan_uploads(manifest, Path("data/corpus"))[0]

    assert upload.metadata["machine_types"] == "SDM-43"
    assert upload.metadata["item_numbers"] == "1.291-101.0"
    assert upload.metadata["revision"] == "3"
    assert upload.metadata["effective_date"] == "2026-03-09"
```

Write `a_manifest_with_one_pdf_entry()` as a local helper in that module, following the builder style already used there.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/corpus/test_manifest.py tests/corpus/test_upload.py -v`
Expected: FAIL — `extra fields not permitted` on the four new keys, and `MANIFEST_VERSION == 1`.

- [ ] **Step 3: Extend `ManifestEntry` and bump the version**

In `src/fleet_copilot/corpus/manifest.py`, change the constant:

```python
MANIFEST_VERSION: Final = 2
"""Schema version of ``data/manifest.json``.

Bumped when the entry shape changes, so a reader can tell an old manifest from a
corrupt one. Version 2 added the four front-matter keys that rendering strips
out of a published PDF (ADR 0006).
"""
```

Add the fields to `ManifestEntry`, after `bytes`, and `from datetime import date` to the imports:

```python
    machine_types: tuple[str, ...] = ()
    item_numbers: tuple[str, ...] = ()
    revision: Annotated[int, Field(ge=1)]
    effective_date: date
    """The four front-matter keys a chunk needs and a rendered document loses.

    Not a convenience copy: the PDF and DOCX renderers drop the front matter, so
    for the 25 converted documents this manifest and the blob metadata beside
    them are the only places these values exist outside the Markdown source that
    ADR 0003 deliberately does not upload.
    """
```

- [ ] **Step 4: Populate them in `writer.py`**

Extend the `ManifestEntry(...)` call at line 87 with four arguments from the front matter:

```python
machine_types = (document.plan.front_matter.machine_types,)
item_numbers = (document.plan.front_matter.item_numbers,)
revision = (document.plan.front_matter.revision,)
effective_date = (document.plan.front_matter.effective_date,)
```

`FrontMatter._sorted_and_unique` has already sorted and deduplicated the two lists, so the manifest inherits a deterministic order without re-sorting here.

- [ ] **Step 5: Write them as blob metadata in `upload.py`**

In `plan_uploads`, extend the `metadata` dict:

```python
metadata = (
    {
        CHECKSUM_METADATA_KEY: entry.sha256,
        "doc_id": entry.doc_id,
        "type": entry.type.value,
        "language": entry.language.value,
        "format": entry.format.value,
        # Blob metadata is sent as HTTP headers, so these must be
        # ASCII scalars. Machine codes and item numbers are ASCII
        # already; the lists are comma-joined and the date is
        # ISO-8601, which keeps the whole set well inside the 8 KB
        # limit for 120 documents.
        "machine_types": ",".join(entry.machine_types),
        "item_numbers": ",".join(entry.item_numbers),
        "revision": str(entry.revision),
        "effective_date": entry.effective_date.isoformat(),
    },
)
```

- [ ] **Step 6: Run them and watch them pass**

Run: `uv run pytest tests/corpus/ -v`
Expected: all pass.

- [ ] **Step 7: Regenerate the manifest**

```bash
just corpus
git diff --stat data/
```

Expected: **`data/manifest.json` is the only changed file.** Document bytes are unaffected by a manifest change, so no `.md`, `.pdf` or `.docx` should appear in the diff. If one does, something in this task touched the renderers — stop and find it, because ADR 0003's reproducibility test is what would catch it next and it is cheaper to catch here.

- [ ] **Step 8: Confirm the corpus still reproduces**

Run: `just corpus-check`
Then: `uv run pytest tests/corpus/ -v`
Expected: no drift reported, all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/fleet_copilot/corpus/ tests/corpus/ data/manifest.json
git commit -m "feat(corpus): carry the four front-matter keys rendering strips

A chunk must record machine_types, item_numbers, revision and effective_date
(ADR 0006), and none of them survive into a published PDF -- decompressing the
text streams finds the prose and no YAML keys. They travel in the manifest and
in blob metadata instead, so an ingest reading only the container reaches the
same values a laptop does.

MANIFEST_VERSION goes to 2. Document bytes and their hashes are unchanged; only
the manifest differs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The parsed-document contract

Pure. No SDK import, no network. Every later task depends on this file.

**Files:**
- Create: `src/fleet_copilot/ingest/parse.py`
- Test: `tests/ingest/test_parse.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class ParseError(RuntimeError)`
  - `class ParserId(StrEnum)`: `AZURE_LAYOUT = "azure-document-intelligence"`, `MARKDOWN = "markdown"`, `LOCAL = "local"`
  - `class BlockRole(StrEnum)`: `TITLE`, `SECTION_HEADING`, `PARAGRAPH`, `TABLE`, `PAGE_HEADER`, `PAGE_FOOTER`, `PAGE_NUMBER`, `FOOTNOTE`, `FORMULA_BLOCK`
  - `HEADING_ROLES: frozenset[BlockRole]`
  - `class ParsedPage(BaseModel)`: `page_number`, `start`, `end`
  - `class ParsedBlock(BaseModel)`: `role`, `text`, `start`, `end`, `page_number`
  - `class ParsedDocument(BaseModel)`: `doc_id`, `source_sha256`, `parser`, `model_id`, `api_version`, `content`, `blocks`, `pages`, `parsed_at`; methods `headings()` and `tables()`
  - `class DocumentParser(Protocol)`: `async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_parse.py`:

```python
"""The contract every parser produces and every chunker consumes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParserId,
)

CONTENT = "# Manual\n\nBody text here.\n"


def a_document(**overrides: object) -> ParsedDocument:
    """Build a valid ParsedDocument, with fields replaced for the case at hand."""
    fields: dict[str, object] = {
        "doc_id": "manual",
        "source_sha256": "0" * 64,
        "parser": ParserId.MARKDOWN,
        "model_id": "markdown",
        "api_version": None,
        "content": CONTENT,
        "blocks": (
            ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
            ParsedBlock(
                role=BlockRole.PARAGRAPH, text="Body text here.", start=10, end=25, page_number=1
            ),
        ),
        "pages": (ParsedPage(page_number=1, start=0, end=len(CONTENT)),),
        "parsed_at": datetime(2026, 9, 17, tzinfo=UTC),
    }
    fields.update(overrides)
    return ParsedDocument.model_validate(fields)


def test_a_valid_document_round_trips() -> None:
    document = a_document()
    first = document.blocks[0]

    assert document.content[first.start : first.end] == "# Manual"


def test_a_block_whose_span_does_not_match_its_text_is_rejected() -> None:
    """Every chunker slices content by offset; a lying span mis-slices silently."""
    with pytest.raises(ValidationError, match="does not match"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH, text="Body text here.", start=0, end=15, page_number=1
                ),
            )
        )


def test_blocks_must_be_in_reading_order() -> None:
    """Chunk indices come from block order, and an index ordered wrongly is wrong."""
    with pytest.raises(ValidationError, match="reading order"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH,
                    text="Body text here.",
                    start=10,
                    end=25,
                    page_number=1,
                ),
                ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
            )
        )


def test_blocks_must_not_overlap() -> None:
    """A table's cells arrive as paragraphs as well as in the table.

    Emitting both would double every table's text: once inside a TABLE chunk and
    once as loose prose, so a retriever would see each row twice and a citation
    could land on either copy.
    """
    with pytest.raises(ValidationError, match="overlaps"):
        a_document(
            blocks=(
                ParsedBlock(role=BlockRole.TITLE, text="# Manual", start=0, end=8, page_number=1),
                ParsedBlock(role=BlockRole.PARAGRAPH, text="Manual", start=2, end=8, page_number=1),
            )
        )


def test_a_block_reaching_past_the_content_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reaches past"):
        a_document(
            blocks=(
                ParsedBlock(
                    role=BlockRole.PARAGRAPH, text="x" * 99, start=0, end=99, page_number=1
                ),
            )
        )


def test_headings_returns_titles_and_section_headings_only() -> None:
    assert tuple(block.role for block in a_document().headings()) == (BlockRole.TITLE,)


def test_tables_returns_table_blocks_only() -> None:
    assert a_document().tables() == ()


def test_a_naive_parsed_at_is_rejected() -> None:
    """Parses run on more than one machine; a naive timestamp cannot be ordered."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        a_document(parsed_at=datetime(2026, 9, 17))  # noqa: DTZ001 - the point of the test
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.parse'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/parse.py`**

```python
"""What a parsed document is, independently of what parsed it.

One ``content`` string, and blocks that are spans into it. Nothing else holds
text. Three chunking strategies will slice this, and they slice one coordinate
system rather than each re-deriving offsets and getting it differently wrong.

All three parsers produce this same shape, so a chunker cannot tell a Markdown
document from an OCR'd one -- which is what makes a strategy comparison across
the whole corpus mean anything.

Deliberately free of any SDK import: this module is the contract, and the things
that import it -- tests, chunkers, the eval harness -- must not need an Azure
account to read it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ParseError(RuntimeError):
    """Raised when a document cannot be parsed honestly."""


class ParserId(StrEnum):
    """Which implementation produced a document."""

    AZURE_LAYOUT = "azure-document-intelligence"
    MARKDOWN = "markdown"
    LOCAL = "local"


class BlockRole(StrEnum):
    """What a span of content is.

    Seven of these mirror Document Intelligence's paragraph roles one for one,
    including the three kinds of page furniture. Those are kept rather than
    stripped: removing them would shift every later offset, and a chunker that
    wants to skip them can filter on the role.

    There is deliberately no LIST_ITEM. The service has no list role, so a
    Markdown parser that emitted one would produce output the Azure parser could
    not match, and a chunker would behave differently depending on which parser
    ran. ADR 0006 keeps step lists atomic by reading Markdown ordered-list syntax
    out of ``content``, which both parsers produce identically.
    """

    TITLE = "title"
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    FORMULA_BLOCK = "formula_block"


HEADING_ROLES: frozenset[BlockRole] = frozenset({BlockRole.TITLE, BlockRole.SECTION_HEADING})
"""Roles a breadcrumb is built from. The contextual-header strategy reads this."""


class ParsedPage(BaseModel):
    """One page, and the slice of content it accounts for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_number: Annotated[int, Field(ge=1)]
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]


class ParsedBlock(BaseModel):
    """A span of content, and what kind of thing it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: BlockRole
    text: NonEmptyStr
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    page_number: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _span_matches_text(self) -> Self:
        """Keep the span and the text in agreement.

        Chunkers slice ``content`` by offset and never read ``text``; a block
        whose two disagree produces a chunk that looks right in the model and
        wrong in the index, and nothing between here and a human reading a
        citation would notice.
        """
        if self.end <= self.start:
            msg = f"end ({self.end}) must be greater than start ({self.start})"
            raise ValueError(msg)
        if self.end - self.start != len(self.text):
            msg = (
                f"span {self.start}:{self.end} covers {self.end - self.start} characters "
                f"but text is {len(self.text)} characters long"
            )
            raise ValueError(msg)
        return self


class ParsedDocument(BaseModel):
    """A document after parsing, before it is chunked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: NonEmptyStr
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    parser: ParserId
    model_id: NonEmptyStr
    api_version: str | None = None
    content: NonEmptyStr
    blocks: tuple[ParsedBlock, ...]
    pages: tuple[ParsedPage, ...]
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("parsed_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        """Reject naive timestamps, matching Document.ingested_at.

        A parse may run on a laptop in one timezone and in CI in another; a naive
        timestamp cannot be ordered against one from the other without guessing
        its offset.
        """
        if value.tzinfo is None:
            msg = "parsed_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _blocks_partition_the_content_they_cover(self) -> Self:
        """Check every block against the content it claims to describe.

        Three failures, all invisible downstream. A block whose text is not what
        its span covers mis-slices every chunk built from it. Blocks out of
        reading order give chunk indices that scramble a multi-chunk answer while
        every individual citation still looks correct. Overlapping blocks
        duplicate text -- the service reports a table's cells as paragraphs as
        well as in the table itself -- so a retriever would see each row twice
        and a citation could land on either copy.
        """
        previous_start = -1
        previous_end = 0
        for block in self.blocks:
            if block.end > len(self.content):
                msg = (
                    f"block {block.start}:{block.end} reaches past content "
                    f"of {len(self.content)} characters"
                )
                raise ValueError(msg)
            if self.content[block.start : block.end] != block.text:
                msg = f"block at {block.start}:{block.end} does not match the content it spans"
                raise ValueError(msg)
            if block.start < previous_start:
                msg = f"block at {block.start} is not in reading order"
                raise ValueError(msg)
            if block.start < previous_end:
                msg = f"block at {block.start} overlaps the one ending at {previous_end}"
                raise ValueError(msg)
            previous_start = block.start
            previous_end = block.end
        return self

    def headings(self) -> tuple[ParsedBlock, ...]:
        """Every title and section heading, in reading order."""
        return tuple(block for block in self.blocks if block.role in HEADING_ROLES)

    def tables(self) -> tuple[ParsedBlock, ...]:
        """Every table, in reading order. ADR 0006 makes each one its own chunk."""
        return tuple(block for block in self.blocks if block.role is BlockRole.TABLE)


@runtime_checkable
class DocumentParser(Protocol):
    """Turns bytes into a :class:`ParsedDocument`.

    ``parse`` is async because the Azure implementation is a network call and the
    others read files; none may hold the event loop. A parser that cannot read
    what it was given raises :class:`ParseError` rather than returning an empty
    document -- an empty document is indistinguishable from a blank page, and
    this corpus contains neither.
    """

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument: ...
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_parse.py -v`
Expected: 9 passed.

- [ ] **Step 5: Type-check, because the Protocol is the point**

Run: `uv run mypy`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/parse.py tests/ingest/test_parse.py
git commit -m "feat(ingest): define what a parsed document is

One content string, blocks as spans into it, nothing else holding text. All
three parsers produce this shape, so a chunker cannot tell a Markdown document
from an OCR'd one -- which is what makes a strategy comparison across the whole
corpus mean anything.

Blocks may not overlap. The service reports a table's cells as paragraphs as
well as in the table, and emitting both would put every row in the index twice
with a citation able to land on either copy.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The Document Intelligence mapper

The pure half of the Azure parser. Tested entirely against Task 2's fixtures — no network, no account.

**Files:**
- Create: `src/fleet_copilot/ingest/layout.py`
- Test: `tests/ingest/test_layout.py`

**Interfaces:**
- Consumes: Task 5's contract; Task 2's fixtures.
- Produces: `MODEL_ID: Final = "prebuilt-layout"`, `ROLE_BY_DI_NAME: Mapping[str, BlockRole]`, and `layout_from_analyze_result(payload: Mapping[str, Any], *, doc_id: str, source_sha256: str) -> ParsedDocument`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_layout.py`:

```python
"""The Document Intelligence mapper, against real service output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.ingest.layout import layout_from_analyze_result
from fleet_copilot.ingest.parse import BlockRole, ParsedDocument, ParseError, ParserId

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
STEMS = (
    "sdm-43-service-manual",
    "service-report-sd50b-2026-10142-17",
    "sdr-90-operator-manual-hu",
)


def fixture(stem: str) -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return payload


def parsed(stem: str) -> ParsedDocument:
    return layout_from_analyze_result(fixture(stem), doc_id=stem, source_sha256="a" * 64)


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_is_a_verbatim_slice_of_content(stem: str) -> None:
    """The invariant the whole design rests on, checked against real output.

    ParsedDocument validates this itself, so a mapper that took text from
    paragraph.content rather than from the span fails here rather than produce
    chunks quoting something the document does not say.
    """
    document = parsed(stem)

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.parametrize("stem", STEMS)
def test_the_parser_and_model_are_recorded(stem: str) -> None:
    document = parsed(stem)

    assert document.parser is ParserId.AZURE_LAYOUT
    assert document.model_id == "prebuilt-layout"
    assert document.api_version == "2024-11-30"


def test_the_service_manual_yields_headings() -> None:
    """Headings are what two of the three chunking strategies split on."""
    headings = parsed("sdm-43-service-manual").headings()

    assert headings, "prebuilt-layout returned no headings for a document that has six"
    assert any("Safety" in block.text for block in headings)


def test_the_service_manual_yields_a_table_block() -> None:
    """ADR 0006 makes each table its own chunk, so the mapper must emit one."""
    tables = parsed("sdm-43-service-manual").tables()

    assert tables, "the service-interval table did not survive mapping"
    assert "|" in tables[0].text, "a table block should be the Markdown table, pipes and all"


def test_table_cells_are_not_also_emitted_as_paragraphs() -> None:
    """The service reports a table's cells as paragraphs as well as in the table.

    Emitting both would put every interval row in the index twice, once inside
    the table chunk and once as loose prose. ParsedDocument rejects overlapping
    blocks, so this passing means the mapper dropped the duplicates.
    """
    document = parsed("sdm-43-service-manual")
    table = document.tables()[0]

    inside = [
        block
        for block in document.blocks
        if block.role is not BlockRole.TABLE
        and block.start >= table.start
        and block.end <= table.end
    ]
    assert inside == []


def test_the_scanned_report_yields_blocks() -> None:
    """An image-only PDF. Any block at all came out of OCR."""
    assert parsed("service-report-sd50b-2026-10142-17").blocks


def test_an_empty_content_payload_is_refused() -> None:
    """A successful call that returned nothing is the F0 failure mode.

    It must not become an empty ParsedDocument that gets cached and then read
    forever by every chunker as a document with nothing in it.
    """
    with pytest.raises(ParseError, match="no content"):
        layout_from_analyze_result(
            {"apiVersion": "2024-11-30", "modelId": "prebuilt-layout", "content": ""},
            doc_id="empty",
            source_sha256="a" * 64,
        )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.layout'`

- [ ] **Step 3: Implement the mapper half of `src/fleet_copilot/ingest/layout.py`**

```python
"""Azure AI Document Intelligence, and the mapping from its output to ours.

Split the way corpus/upload.py is split: everything with a decision in it is a
pure function over the response payload, tested against committed fixtures, and
only :class:`AzureLayoutParser` touches the network.

The SDK is imported inside the method that needs it. Parsing is a dev-time
operation -- the API image installs with --no-dev -- so this module has to stay
importable without azure-ai-documentintelligence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseError,
    ParserId,
)

MODEL_ID: Final = "prebuilt-layout"
"""Layout, not Read. Read is OCR alone and would leave us reconstructing
structure by counting '#' characters -- of which the five scanned PDFs in this
corpus contain none."""

ROLE_BY_DI_NAME: Final[Mapping[str, BlockRole]] = {
    "title": BlockRole.TITLE,
    "sectionHeading": BlockRole.SECTION_HEADING,
    "pageHeader": BlockRole.PAGE_HEADER,
    "pageFooter": BlockRole.PAGE_FOOTER,
    "pageNumber": BlockRole.PAGE_NUMBER,
    "footnote": BlockRole.FOOTNOTE,
    "formulaBlock": BlockRole.FORMULA_BLOCK,
}
"""The service's seven paragraph roles. A paragraph carrying no role at all is
ordinary body text -- the common case -- and maps to PARAGRAPH."""


def _span_bounds(spans: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Return the outer bounds of ``spans``.

    A paragraph is usually one span. Where it is more -- one the service split
    across a column break -- taking the outer bounds keeps text equal to
    content[start:end], which concatenating the spans would not.
    """
    offsets = [int(span["offset"]) for span in spans]
    ends = [int(span["offset"]) + int(span["length"]) for span in spans]
    return min(offsets), max(ends)


def _page_number(element: Mapping[str, Any]) -> int:
    """Return the page an element sits on, defaulting to the first."""
    regions = element.get("boundingRegions") or [{"pageNumber": 1}]
    return int(regions[0]["pageNumber"])


def _table_blocks(
    payload: Mapping[str, Any], content: str
) -> tuple[tuple[ParsedBlock, ...], tuple[tuple[int, int], ...]]:
    """Return the table blocks, and the spans they occupy.

    The spans come back too so the paragraph pass can drop the cells the service
    reports twice -- once as paragraphs, once inside the table.
    """
    blocks: list[ParsedBlock] = []
    spans: list[tuple[int, int]] = []
    for table in payload.get("tables") or []:
        table_spans = table.get("spans") or []
        if not table_spans:
            continue
        start, end = _span_bounds(table_spans)
        text = content[start:end]
        if not text.strip():
            continue
        spans.append((start, end))
        blocks.append(
            ParsedBlock(
                role=BlockRole.TABLE,
                text=text,
                start=start,
                end=end,
                page_number=_page_number(table),
            )
        )
    return tuple(blocks), tuple(spans)


def _within(start: int, end: int, spans: Sequence[tuple[int, int]]) -> bool:
    """Whether ``start:end`` falls inside any of ``spans``."""
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)


def layout_from_analyze_result(
    payload: Mapping[str, Any], *, doc_id: str, source_sha256: str
) -> ParsedDocument:
    """Map one ``AnalyzeResult`` payload onto :class:`ParsedDocument`.

    ``payload`` is ``AnalyzeResult.as_dict()``, which is also exactly what the
    cache stores -- so this runs identically on a fresh response and a cached
    one, and a change here never costs an analyse call.
    """
    content = str(payload.get("content") or "")
    if not content:
        msg = (
            f"{doc_id}: the analyse call succeeded but returned no content. "
            "That is what the F0 tier does; check the account is S0."
        )
        raise ParseError(msg)

    pages: list[ParsedPage] = []
    for page in payload.get("pages") or []:
        spans = page.get("spans") or [{"offset": 0, "length": len(content)}]
        page_start, page_end = _span_bounds(spans)
        pages.append(
            ParsedPage(page_number=int(page["pageNumber"]), start=page_start, end=page_end)
        )

    table_blocks, table_spans = _table_blocks(payload, content)
    blocks: list[ParsedBlock] = list(table_blocks)

    for paragraph in payload.get("paragraphs") or []:
        spans = paragraph.get("spans") or []
        if not spans:
            continue
        start, end = _span_bounds(spans)
        # Cells arrive as paragraphs as well as inside the table. Keeping both
        # would put every row in the index twice and let a citation land on
        # either copy; ParsedDocument rejects the overlap outright.
        if _within(start, end, table_spans):
            continue
        text = content[start:end]
        if not text:
            continue
        blocks.append(
            ParsedBlock(
                role=ROLE_BY_DI_NAME.get(str(paragraph.get("role") or ""), BlockRole.PARAGRAPH),
                # Derived from the span, never taken from paragraph["content"]:
                # in Markdown mode the service returns the undecorated text
                # there, which does not always coincide with what the span
                # covers. Deriving makes the span invariant true by construction.
                text=text,
                start=start,
                end=end,
                page_number=_page_number(paragraph),
            )
        )

    blocks.sort(key=lambda block: block.start)

    return ParsedDocument(
        doc_id=doc_id,
        source_sha256=source_sha256,
        parser=ParserId.AZURE_LAYOUT,
        model_id=str(payload.get("modelId") or MODEL_ID),
        api_version=str(payload["apiVersion"]) if payload.get("apiVersion") else None,
        content=content,
        blocks=tuple(blocks),
        pages=tuple(pages),
        parsed_at=datetime.now(UTC),
    )
```

Task 10 adds `AzureLayoutParser` to this same module and will need `import hashlib` at the top then. Do not add it now — ruff flags an unused import and `just lint` would fail.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_layout.py -v`
Expected: all pass.

If a document raises `overlaps` from `ParsedDocument`, either the service returned two tables sharing a span, or a paragraph straddles a table boundary rather than sitting inside it. Widen `_within` to drop any paragraph that *intersects* a table span rather than only one contained by it — but look at the actual spans first, because a straddling paragraph may mean the table bounds are wrong.

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/layout.py tests/ingest/test_layout.py
git commit -m "feat(ingest): map Document Intelligence layout onto ParsedDocument

Block text is derived from the span rather than copied from paragraph.content:
in Markdown mode the service returns undecorated text there, which does not
always coincide with what the span covers.

Tables are emitted as their own blocks and their cells dropped from the
paragraph pass. The service reports both, and keeping both would put every
interval row in the index twice with a citation able to land on either copy.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The Markdown parser

95 of the 120 documents. They carry their headings in the text already, so paying per page to OCR them would be absurd.

**Files:**
- Create: `src/fleet_copilot/ingest/markdown.py`
- Test: `tests/ingest/test_markdown.py`

**Interfaces:**
- Consumes: Task 5's contract.
- Produces: `class MarkdownParser` implementing `DocumentParser`; `strip_front_matter(text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_markdown.py`:

```python
"""The native Markdown parser: 95 of the 120 documents take this path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fleet_copilot.ingest.markdown import MarkdownParser, strip_front_matter
from fleet_copilot.ingest.parse import BlockRole, DocumentParser, ParseError, ParserId

CORPUS = Path(__file__).resolve().parents[2] / "data" / "corpus" / "markdown"
MANUAL = CORPUS / "sdm-43-service-manual.md"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"


def test_front_matter_is_removed_from_the_content() -> None:
    """Front matter is metadata, not prose.

    Left in, the fixed-size baseline would spend its first chunk on YAML and the
    contextual-header strategy would embed the doc_id twice.
    """
    body = strip_front_matter(MANUAL.read_text(encoding="utf-8"))

    assert body.startswith("# Single-disc machine SDM-43")
    assert "doc_id:" not in body


def test_a_document_without_front_matter_is_left_alone() -> None:
    assert strip_front_matter("# Title\n\nBody.\n") == "# Title\n\nBody.\n"


@pytest.mark.asyncio
async def test_every_block_is_a_verbatim_slice_of_content() -> None:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.asyncio
async def test_atx_levels_become_title_and_section_heading() -> None:
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    roles = [block.role for block in document.headings()]
    assert roles[0] is BlockRole.TITLE
    assert roles.count(BlockRole.TITLE) == 1, "a document has one title and many sections"
    assert BlockRole.SECTION_HEADING in roles
    assert any("Safety" in block.text for block in document.headings())


@pytest.mark.asyncio
async def test_a_markdown_table_becomes_one_table_block() -> None:
    """The service-interval table, pipes and all, as a single chunkable unit."""
    document = await MarkdownParser().parse(
        MANUAL.read_bytes(), doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    tables = document.tables()
    assert len(tables) == 1
    assert tables[0].text.count("\n") >= 5, "header, separator and five interval rows"
    assert "1000 h" in tables[0].text


@pytest.mark.asyncio
async def test_the_source_hash_covers_the_original_bytes() -> None:
    """The manifest hashes the file, front matter included. Hashing the stripped
    body instead would make every cache lookup and drift check miss."""
    data = MANUAL.read_bytes()

    document = await MarkdownParser().parse(
        data, doc_id="sdm-43-service-manual", content_type=MARKDOWN_TYPE
    )

    assert document.source_sha256 == hashlib.sha256(data).hexdigest()
    assert document.parser is ParserId.MARKDOWN


@pytest.mark.asyncio
async def test_the_whole_markdown_corpus_parses() -> None:
    """Every Markdown document, because the failure mode here is one odd file.

    ParsedDocument validates spans, ordering and overlap on construction, so this
    exercises all three against 120 real documents in two languages.
    """
    parser = MarkdownParser()
    paths = sorted(CORPUS.glob("*.md"))
    assert len(paths) == 120

    for path in paths:
        document = await parser.parse(
            path.read_bytes(), doc_id=path.stem, content_type=MARKDOWN_TYPE
        )
        assert document.blocks, f"{path.name} produced no blocks"


@pytest.mark.asyncio
async def test_a_document_that_is_only_front_matter_raises() -> None:
    with pytest.raises(ParseError, match="no content"):
        await MarkdownParser().parse(
            b"---\ndoc_id: x\n---\n", doc_id="x", content_type=MARKDOWN_TYPE
        )


def test_markdown_parser_satisfies_the_protocol() -> None:
    assert isinstance(MarkdownParser(), DocumentParser)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_markdown.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.markdown'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/markdown.py`**

```python
"""Parse the Markdown documents natively.

Ninety-five of the corpus's 120 documents are Markdown, and their structure is
already in the text: ATX headings mark the sections, pipe rows mark the tables.
Sending them to Document Intelligence would pay per page to recover what is
sitting in plain sight, and would OCR prose we wrote ourselves.

The output is the same ParsedDocument the Azure parser produces, so a chunker
cannot tell which path a document came down.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Final

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseError,
    ParserId,
)

MODEL_ID: Final = "markdown"
FRONT_MATTER_FENCE: Final = "---\n"


def strip_front_matter(text: str) -> str:
    """Return ``text`` without its YAML front matter block.

    The metadata is not prose. Left in, the fixed-size baseline would spend its
    first chunk on YAML, and the contextual-header strategy would embed the
    doc_id once in the header and once in the body.
    """
    if not text.startswith(FRONT_MATTER_FENCE):
        return text
    rest = text[len(FRONT_MATTER_FENCE) :]
    end = rest.find("\n" + FRONT_MATTER_FENCE)
    if end == -1:
        return text
    return rest[end + 1 + len(FRONT_MATTER_FENCE) :].lstrip("\n")


def _line_spans(content: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, text)`` per line, with the line ending excluded.

    Offsets accumulate as the lines are walked rather than being searched for:
    two identical bullets in one document -- which the fragment banks produce
    routinely -- would both find the first occurrence.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip("\r")
        spans.append((cursor, cursor + len(stripped), stripped))
        cursor += len(line)
    return spans


def _classify(line: str) -> BlockRole | None:
    """Return the role of ``line``, or None for a blank one."""
    if not line.strip():
        return None
    if line.startswith("#"):
        level = len(line) - len(line.lstrip("#"))
        return BlockRole.TITLE if level == 1 else BlockRole.SECTION_HEADING
    if line.lstrip().startswith("|"):
        return BlockRole.TABLE
    return BlockRole.PARAGRAPH


def _blocks(content: str) -> tuple[ParsedBlock, ...]:
    """Group lines into blocks: headings alone, everything else in runs."""
    blocks: list[ParsedBlock] = []
    run_role: BlockRole | None = None
    run_start = 0
    run_end = 0

    def close() -> None:
        nonlocal run_role
        if run_role is not None:
            blocks.append(
                ParsedBlock(
                    role=run_role,
                    text=content[run_start:run_end],
                    start=run_start,
                    end=run_end,
                    page_number=1,
                )
            )
            run_role = None

    for start, end, line in _line_spans(content):
        role = _classify(line)
        if role in (BlockRole.TITLE, BlockRole.SECTION_HEADING):
            close()
            blocks.append(
                ParsedBlock(role=role, text=content[start:end], start=start, end=end, page_number=1)
            )
            continue
        if role is not run_role:
            close()
        if role is not None:
            if run_role is None:
                run_role = role
                run_start = start
            run_end = end

    close()
    return tuple(blocks)


class MarkdownParser:
    """Parses Markdown natively. Implements :class:`DocumentParser`."""

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Parse ``data``.

        ``content_type`` is unused -- the caller dispatched on format to pick
        this parser -- but is in the signature because DocumentParser requires it.
        """
        text = await asyncio.to_thread(data.decode, "utf-8")
        content = strip_front_matter(text)
        if not content.strip():
            msg = f"{doc_id}: no content once the front matter is removed"
            raise ParseError(msg)

        return ParsedDocument(
            doc_id=doc_id,
            # The manifest hashes the file as written, front matter included.
            # Hashing the stripped body instead would make every cache lookup and
            # every drift check miss.
            source_sha256=hashlib.sha256(data).hexdigest(),
            parser=ParserId.MARKDOWN,
            model_id=MODEL_ID,
            api_version=None,
            content=content,
            blocks=_blocks(content),
            # Markdown has no pages. One page covering the whole document keeps
            # the shape identical to the Azure parser's rather than making every
            # consumer special-case an empty tuple.
            pages=(ParsedPage(page_number=1, start=0, end=len(content)),),
            parsed_at=datetime.now(UTC),
        )
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_markdown.py -v`
Expected: 9 passed, including the sweep over all 120 documents.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/markdown.py tests/ingest/test_markdown.py
git commit -m "feat(ingest): parse the Markdown corpus natively

Ninety-five of the 120 documents carry their structure in the text already.
Sending them to Document Intelligence would pay per page to recover what is
sitting in plain sight, and would OCR prose we wrote ourselves.

Output is the same ParsedDocument the Azure parser produces, so a chunker cannot
tell which path a document came down -- which is what makes a strategy
comparison across the whole corpus mean anything.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The local fallback

**Files:**
- Create: `src/fleet_copilot/ingest/fallback.py`
- Test: `tests/ingest/test_fallback.py`

**Interfaces:**
- Consumes: Task 5's contract.
- Produces: `class LocalParser` implementing `DocumentParser`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_fallback.py`:

```python
"""The offline parser: what it can do, and what it must refuse to fake."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_copilot.ingest.fallback import LocalParser
from fleet_copilot.ingest.parse import DocumentParser, ParsedDocument, ParseError, ParserId

PUBLISHED = Path(__file__).resolve().parents[2] / "data" / "corpus" / "published"

TEXT_PDF = "sdm-43-service-manual.pdf"
SCANNED_PDF = "service-report-sd50b-2026-10142-17.pdf"
DOCX = "sdr-90-operator-manual-hu.docx"

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.asyncio
async def test_a_pdf_with_a_text_layer_is_parsed() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    assert isinstance(document, ParsedDocument)
    assert document.parser is ParserId.LOCAL
    assert "squeegee" in document.content.lower()


@pytest.mark.asyncio
async def test_every_block_is_a_verbatim_slice_of_content() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.asyncio
async def test_a_scanned_pdf_raises_instead_of_returning_nothing() -> None:
    """The whole reason the Azure path is worth paying for.

    Five of the twenty PDFs in this corpus have no text layer, and one hides a
    planted prompt injection. A fallback that returned an empty document for them
    would keep the suite green while the OCR path went untested.
    """
    with pytest.raises(ParseError, match="no text layer"):
        await LocalParser().parse(
            (PUBLISHED / SCANNED_PDF).read_bytes(),
            doc_id="service-report-sd50b-2026-10142-17",
            content_type=PDF_TYPE,
        )


@pytest.mark.asyncio
async def test_a_docx_is_parsed() -> None:
    document = await LocalParser().parse(
        (PUBLISHED / DOCX).read_bytes(), doc_id="sdr-90-operator-manual-hu", content_type=DOCX_TYPE
    )

    assert document.content.strip()


@pytest.mark.asyncio
async def test_it_produces_no_headings_and_no_tables() -> None:
    """Not a silent limitation. A chunking comparison run against this parser
    would measure the fallback rather than the pipeline, and this says so."""
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    assert document.headings() == ()
    assert document.tables() == ()


@pytest.mark.asyncio
async def test_an_unsupported_content_type_raises() -> None:
    with pytest.raises(ParseError, match="cannot parse"):
        await LocalParser().parse(b"whatever", doc_id="x", content_type="image/png")


def test_local_parser_satisfies_the_parser_protocol() -> None:
    """mypy checks this statically; this catches signature drift at runtime."""
    assert isinstance(LocalParser(), DocumentParser)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.fallback'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/fallback.py`**

```python
"""A parser that needs no Azure account, and refuses to pretend it is one.

This is what unit tests use, so CI never calls the service. It is deliberately a
weaker parser, not an equivalent one: it produces no heading roles and no table
structure, and it raises on a PDF with no text layer rather than returning an
empty document.

That last refusal is the point. Five of the twenty PDFs in this corpus are
image-only, and one carries a planted prompt injection reachable by no other
route. A fallback that quietly returned nothing for them would keep the suite
green while the OCR path went untested.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from datetime import UTC, datetime
from typing import Final

from fleet_copilot.ingest.parse import (
    BlockRole,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseError,
    ParserId,
)

PDF_CONTENT_TYPE: Final = "application/pdf"
DOCX_CONTENT_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MODEL_ID: Final = "local"

PIECE_SEPARATOR: Final = "\n\n"
"""Joins extracted pieces. Two characters, counted into every offset after the
first, which is why offsets are accumulated as content is built rather than
searched for afterwards."""


def _pdf_pages(data: bytes) -> list[str]:
    """Extract text per page. Blocking; call via :func:`asyncio.to_thread`."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        return [str(page.get_text()) for page in document]


def _docx_paragraphs(data: bytes) -> list[str]:
    """Extract non-empty paragraph text. Blocking; see above."""
    import docx

    return [
        paragraph.text for paragraph in docx.Document(io.BytesIO(data)).paragraphs if paragraph.text
    ]


def _assemble(doc_id: str, source_sha256: str, pieces: list[tuple[str, int]]) -> ParsedDocument:
    """Build a document from ``(text, page_number)`` pieces, in order.

    Offsets accumulate as the content string is built rather than being searched
    for afterwards: two identical paragraphs on one page -- which handover notes
    produce routinely -- would both find the first occurrence.
    """
    content_parts: list[str] = []
    blocks: list[ParsedBlock] = []
    page_bounds: dict[int, tuple[int, int]] = {}
    cursor = 0

    for text, page_number in pieces:
        if cursor:
            content_parts.append(PIECE_SEPARATOR)
            cursor += len(PIECE_SEPARATOR)
        content_parts.append(text)
        blocks.append(
            ParsedBlock(
                role=BlockRole.PARAGRAPH,
                text=text,
                start=cursor,
                end=cursor + len(text),
                page_number=page_number,
            )
        )
        known = page_bounds.get(page_number)
        page_bounds[page_number] = (cursor if known is None else known[0], cursor + len(text))
        cursor += len(text)

    return ParsedDocument(
        doc_id=doc_id,
        source_sha256=source_sha256,
        parser=ParserId.LOCAL,
        model_id=MODEL_ID,
        api_version=None,
        content="".join(content_parts),
        blocks=tuple(blocks),
        pages=tuple(
            ParsedPage(page_number=number, start=start, end=end)
            for number, (start, end) in sorted(page_bounds.items())
        ),
        parsed_at=datetime.now(UTC),
    )


class LocalParser:
    """Parses PDF and DOCX offline. Implements :class:`DocumentParser`."""

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Parse ``data``, or raise :class:`ParseError` saying why it cannot."""
        if content_type == PDF_CONTENT_TYPE:
            pages = await asyncio.to_thread(_pdf_pages, data)
            pieces = [
                (text.strip(), number) for number, text in enumerate(pages, start=1) if text.strip()
            ]
            if not pieces:
                msg = (
                    f"{doc_id}: this PDF has no text layer. Reading it needs OCR, "
                    "which only the Document Intelligence path provides."
                )
                raise ParseError(msg)
        elif content_type == DOCX_CONTENT_TYPE:
            pieces = [(text, 1) for text in await asyncio.to_thread(_docx_paragraphs, data)]
            if not pieces:
                msg = f"{doc_id}: this DOCX contains no paragraph text"
                raise ParseError(msg)
        else:
            msg = (
                f"{doc_id}: cannot parse {content_type!r} locally; "
                f"supported types are {PDF_CONTENT_TYPE} and {DOCX_CONTENT_TYPE}"
            )
            raise ParseError(msg)

        return _assemble(doc_id, hashlib.sha256(data).hexdigest(), pieces)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_fallback.py -v`
Expected: 7 passed.

- [ ] **Step 5: Type-check**

Run: `uv run mypy`
Expected: clean. If `pymupdf` ships no stubs, mypy --strict will object to the untyped import; resolve it with a targeted `# type: ignore[import-untyped]` carrying that code and a reason naming pymupdf, **not** by adding a global mypy override.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/fallback.py tests/ingest/test_fallback.py
git commit -m "feat(ingest): add the offline parser, which refuses to fake OCR

Deliberately weaker than the service and explicit about it: no heading roles,
no tables, and a ParseError on a PDF with no text layer. Five of the twenty PDFs
here are image-only and one hides a planted injection; a fallback that returned
an empty document for them would keep the suite green while the OCR path went
untested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The layout cache

**Files:**
- Create: `src/fleet_copilot/ingest/cache.py`
- Test: `tests/ingest/test_cache.py`

**Interfaces:**
- Consumes: `get_credential` from `fleet_copilot.credentials`.
- Produces: `cache_key(*, source_sha256: str, model_id: str, api_version: str) -> str`; `class LayoutCache(Protocol)` with `get`/`put`; `class LocalLayoutCache(root: Path)`; `class BlobLayoutCache(endpoint: str, container: str)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_cache.py`:

```python
"""The cache that makes a chunking comparison reproducible."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.ingest.cache import LayoutCache, LocalLayoutCache, cache_key

PAYLOAD: dict[str, Any] = {
    "apiVersion": "2024-11-30",
    "modelId": "prebuilt-layout",
    "content": "x",
}


def test_the_key_carries_the_model_and_api_version() -> None:
    """Switching model or API version must invalidate, not silently reuse.

    A cache keyed on content alone would serve prebuilt-read output to a caller
    that had moved to prebuilt-layout, and the difference -- missing headings --
    looks exactly like a document that genuinely has none.
    """
    key = cache_key(source_sha256="a" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    assert key == f"prebuilt-layout/2024-11-30/{'a' * 64}.json"


def test_a_short_hash_is_refused() -> None:
    with pytest.raises(ValueError, match="sha256"):
        cache_key(source_sha256="abc", model_id="prebuilt-layout", api_version="2024-11-30")


@pytest.mark.asyncio
async def test_a_miss_returns_none(tmp_path: Path) -> None:
    assert await LocalLayoutCache(tmp_path).get("prebuilt-layout/2024-11-30/deadbeef.json") is None


@pytest.mark.asyncio
async def test_what_goes_in_comes_back_out(tmp_path: Path) -> None:
    cache = LocalLayoutCache(tmp_path)
    key = cache_key(source_sha256="b" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    await cache.put(key, PAYLOAD)

    assert await cache.get(key) == PAYLOAD


@pytest.mark.asyncio
async def test_it_stores_the_raw_payload_verbatim(tmp_path: Path) -> None:
    """The cache holds AnalyzeResult, not our model of it.

    Re-interpreting a layout -- adding a role, changing how tables serialise --
    must cost nothing, while re-analysing costs money and a round trip. A cache
    of ParsedDocument would invert that.
    """
    cache = LocalLayoutCache(tmp_path)
    key = cache_key(source_sha256="c" * 64, model_id="prebuilt-layout", api_version="2024-11-30")

    await cache.put(key, PAYLOAD)

    assert json.loads((tmp_path / key).read_text(encoding="utf-8")) == PAYLOAD


def test_local_cache_satisfies_the_protocol() -> None:
    assert isinstance(LocalLayoutCache(Path(".")), LayoutCache)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.cache'`

- [ ] **Step 3: Implement `src/fleet_copilot/ingest/cache.py`**

```python
"""Where analysed layouts are kept, so they are analysed once.

The cache stores the raw ``AnalyzeResult`` payload, not our model of it.
Re-interpreting a layout is then free and re-analysing is the only thing that
costs -- the inverse of what caching the parsed model would give.

Two implementations behind one Protocol, the same shape as the parsers:
:class:`LocalLayoutCache` is a directory and is what the tests use, so they need
no Azure account; :class:`BlobLayoutCache` is what a real parse run uses.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

SHA256 = re.compile(r"^[0-9a-f]{64}$")

JSON_INDENT: Final = 2
"""Stored pretty-printed. These files are read by a human exactly once -- when a
mapping is behaving oddly -- and that one read is worth the bytes."""


def cache_key(*, source_sha256: str, model_id: str, api_version: str) -> str:
    """Return the cache path for one analysed document.

    Model id and API version are in the key rather than only in the payload so
    that changing either invalidates by construction. A cache keyed on content
    alone would serve prebuilt-read output to a caller that had moved to
    prebuilt-layout, and the difference -- no headings -- is indistinguishable
    from a document that genuinely has none.
    """
    if not SHA256.match(source_sha256):
        msg = f"source_sha256 must be a 64-character hex sha256, got {source_sha256!r}"
        raise ValueError(msg)
    return f"{model_id}/{api_version}/{source_sha256}.json"


@runtime_checkable
class LayoutCache(Protocol):
    """Stores and retrieves raw analyse payloads by key."""

    async def get(self, key: str) -> Mapping[str, Any] | None: ...

    async def put(self, key: str, payload: Mapping[str, Any]) -> None: ...


def _serialise(payload: Mapping[str, Any]) -> str:
    """Render ``payload`` deterministically, ending in a single newline."""
    return json.dumps(payload, indent=JSON_INDENT, sort_keys=True) + "\n"


def _read(path: Path) -> str | None:
    """Read ``path``, or None if it is not there. Blocking; use to_thread."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, creating parents. Blocking; use to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LocalLayoutCache:
    """A directory. Implements :class:`LayoutCache`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def get(self, key: str) -> Mapping[str, Any] | None:
        text = await asyncio.to_thread(_read, self._root / key)
        if text is None:
            return None
        payload: Mapping[str, Any] = json.loads(text)
        return payload

    async def put(self, key: str, payload: Mapping[str, Any]) -> None:
        await asyncio.to_thread(_write, self._root / key, _serialise(payload))


class BlobLayoutCache:
    """A blob container. Implements :class:`LayoutCache`.

    The SDK is imported per call rather than at module scope so this module stays
    importable without azure-storage-blob, which is a dev-only dependency.
    Authentication is get_credential() and nothing else: ADR 0002 disables
    shared-key access at the resource level, so a connection string here would
    not fail in review, it would fail at runtime.
    """

    def __init__(self, endpoint: str, container: str) -> None:
        self._endpoint = endpoint
        self._container = container

    def _download(self, key: str) -> bytes | None:
        """Blocking; called through to_thread."""
        from azure.core.exceptions import ResourceNotFoundError
        from azure.storage.blob import BlobServiceClient

        from fleet_copilot.credentials import get_credential

        client = BlobServiceClient(account_url=self._endpoint, credential=get_credential())
        blob = client.get_container_client(self._container).get_blob_client(key)
        try:
            data: bytes = blob.download_blob().readall()
        except ResourceNotFoundError:
            return None
        return data

    def _upload(self, key: str, data: bytes) -> None:
        """Blocking; called through to_thread."""
        from azure.storage.blob import BlobServiceClient, ContentSettings

        from fleet_copilot.credentials import get_credential

        client = BlobServiceClient(account_url=self._endpoint, credential=get_credential())
        blob = client.get_container_client(self._container).get_blob_client(key)
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

    async def get(self, key: str) -> Mapping[str, Any] | None:
        data = await asyncio.to_thread(self._download, key)
        if data is None:
            return None
        payload: Mapping[str, Any] = json.loads(data)
        return payload

    async def put(self, key: str, payload: Mapping[str, Any]) -> None:
        await asyncio.to_thread(self._upload, key, _serialise(payload).encode("utf-8"))
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_cache.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/cache.py tests/ingest/test_cache.py
git commit -m "feat(ingest): cache analysed layouts by content hash

Stores the raw AnalyzeResult, not our model of it: re-interpreting a layout must
be free while re-analysing is the thing that costs, and caching the parsed model
would invert that.

Model id and API version are in the key, so moving to a different model or
version invalidates by construction rather than silently serving output whose
missing headings look like a document that has none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: The Azure parser, the dispatch, and the run script

**Files:**
- Modify: `src/fleet_copilot/ingest/layout.py`, `justfile`, `README.md`, `docs/journal.md`
- Create: `src/fleet_copilot/ingest/run.py`, `scripts/parse_corpus.py`
- Test: `tests/ingest/test_run.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `class AzureLayoutParser(endpoint: str, *, api_version: str)` with `analyse()` and `parse()`; `class Analyser(Protocol)`; `class Target`; `analyse_targets()`, `native_targets()`; `class CachedParser`; `class ParseReport`; `async def run(...)`.

- [ ] **Step 1: Add `AzureLayoutParser` to `src/fleet_copilot/ingest/layout.py`**

Add `import hashlib` to the module's imports, then append:

```python
class AzureLayoutParser:
    """Calls prebuilt-layout. Implements :class:`DocumentParser`.

    Holds no client: one is built per call inside an ``async with`` so the
    credential's connection pool is closed rather than left to a finaliser.
    Parsing is a batch of twenty-five documents, not a request path, so a client
    per call costs nothing worth optimising away.
    """

    def __init__(self, endpoint: str, *, api_version: str) -> None:
        self._endpoint = endpoint
        self._api_version = api_version

    async def analyse(self, data: bytes) -> Mapping[str, Any]:
        """Return the raw AnalyzeResult payload -- exactly what the cache stores."""
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import (
            AnalyzeResult,
            DocumentContentFormat,
            StringIndexType,
        )

        from fleet_copilot.credentials import get_async_credential

        credential = get_async_credential()
        async with (
            credential,
            DocumentIntelligenceClient(
                self._endpoint, credential, api_version=self._api_version
            ) as client,
        ):
            poller = await client.begin_analyze_document(
                MODEL_ID,
                body=data,
                output_content_format=DocumentContentFormat.MARKDOWN,
                # Not the SDK default of textElements, which counts grapheme
                # clusters. Python indexes strings by code point, and where the
                # two diverge every offset after the divergence is wrong.
                string_index_type=StringIndexType.UNICODE_CODE_POINT,
            )
            result: AnalyzeResult = await poller.result()
        payload: Mapping[str, Any] = result.as_dict()
        return payload

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Analyse ``data`` and map the result.

        ``content_type`` is unused: the service sniffs the format itself and
        rejects what it cannot read. It is in the signature because
        DocumentParser requires it and the other two parsers need it.
        """
        payload = await self.analyse(data)
        return layout_from_analyze_result(
            payload, doc_id=doc_id, source_sha256=hashlib.sha256(data).hexdigest()
        )
```

- [ ] **Step 2: Write the failing tests**

Create `tests/ingest/test_run.py`:

```python
"""The cache wrapper, and the dispatch that decides which parser runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.ingest.cache import LocalLayoutCache, cache_key
from fleet_copilot.ingest.parse import ParsedDocument
from fleet_copilot.ingest.run import CachedParser, analyse_targets, native_targets

FIXTURE = Path(__file__).parent / "fixtures" / "layout" / "sdm-43-service-manual.json"

DATA = b"stands in for the pdf; the fake analyser ignores it"


class CountingAnalyser:
    """Stands in for AzureLayoutParser, and counts how often it was called."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def analyse(self, data: bytes) -> Mapping[str, Any]:
        self.calls += 1
        return self.payload


def a_payload() -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


@pytest.mark.asyncio
async def test_the_second_parse_of_the_same_bytes_does_not_call_the_service(
    tmp_path: Path,
) -> None:
    """The reason the cache exists.

    Three chunking strategies will read these documents. A comparison is only
    meaningful if every one reads identical input, which a re-analysed document
    does not guarantee.
    """
    analyser = CountingAnalyser(a_payload())
    parser = CachedParser(analyser, LocalLayoutCache(tmp_path), api_version="2024-11-30")

    first = await parser.parse(DATA, doc_id="sdm-43-service-manual", content_type="application/pdf")
    second = await parser.parse(
        DATA, doc_id="sdm-43-service-manual", content_type="application/pdf"
    )

    assert analyser.calls == 1
    assert isinstance(first, ParsedDocument)
    assert first.content == second.content
    assert first.blocks == second.blocks


@pytest.mark.asyncio
async def test_the_cached_entry_lands_under_the_content_hash(tmp_path: Path) -> None:
    parser = CachedParser(
        CountingAnalyser(a_payload()), LocalLayoutCache(tmp_path), api_version="2024-11-30"
    )

    await parser.parse(DATA, doc_id="sdm-43-service-manual", content_type="application/pdf")

    expected = cache_key(
        source_sha256=hashlib.sha256(DATA).hexdigest(),
        model_id="prebuilt-layout",
        api_version="2024-11-30",
    )
    assert (tmp_path / expected).is_file()


def test_only_the_converted_documents_are_sent_to_the_service() -> None:
    """Markdown is parsed natively; sending it would pay per page for nothing."""
    manifest = load_manifest(manifest_path(None))

    assert len(analyse_targets(manifest)) == 25
    assert len(native_targets(manifest)) == 95
    assert len(analyse_targets(manifest)) + len(native_targets(manifest)) == manifest.total
    assert corpus_root(None).is_dir()
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.run'`

- [ ] **Step 4: Implement `src/fleet_copilot/ingest/run.py`**

```python
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
from fleet_copilot.ingest.layout import MODEL_ID, AzureLayoutParser, layout_from_analyze_result
from fleet_copilot.ingest.markdown import MarkdownParser
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
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/ingest/ -v`
Expected: all pass, including the dispatch test reporting 25 and 95.

- [ ] **Step 6: Write `scripts/parse_corpus.py`**

A shim with no logic, mirroring `scripts/upload_corpus.py`. The one difference is `asyncio.run`: `corpus.upload.run` is synchronous and `ingest.run.run` is a coroutine.

```python
"""Parse the corpus: Markdown natively, PDF and DOCX through the service.

    python scripts/parse_corpus.py            report what would be analysed
    python scripts/parse_corpus.py --apply    actually call the service

Needs the Cognitive Services User role on the Document Intelligence account and
Storage Blob Data Contributor on the storage account, both on your own user
principal. Subscription Owner is a management-plane role and does not grant
data-plane access, so an Owner without them gets a 403 from the first request.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest import run as ingest_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="analyse for real; without it the service is never called",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="seed data directory (default: the repository's data/)",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(args.data_dir))
        settings = load_settings()
        report = asyncio.run(
            ingest_run.run(
                manifest,
                corpus_root(args.data_dir),
                settings,
                dry_run=not args.apply,
            )
        )
    except (CorpusDataError, SettingsError) as error:
        print(f"parse failed: {error}", file=sys.stderr)
        return 2

    print(report.describe())
    if report.dry_run:
        print("the service was not called; re-run with --apply to analyse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Add the justfile recipe**

After the `corpus-upload` recipe:

```make
# Dry-run the corpus parse: `just corpus-parse --apply` calls the service.
corpus-parse *args:
    uv run python scripts/parse_corpus.py {{args}}
```

- [ ] **Step 8: Run the real thing, dry then live then dry**

```bash
just corpus-parse
just corpus-parse --apply
just corpus-parse
```

Expected: the first reports `120 documents, 95 parsed natively, would analyse 25, 0 already cached`; the second analyses them; the third reports `120 documents, 95 parsed natively, would analyse 0, 25 already cached`. That third line is the deliverable of this plan.

- [ ] **Step 9: Run the full gate**

Run: `just check`
Then: `uv run pre-commit run --all-files`
Expected: both clean. Paste the output; do not assert it passed.

- [ ] **Step 10: Update the docs**

Add a table to `README.md` after the corpus counts table (`README.md:58-63`), in the same style:

```markdown
| Parsing | |
| --- | --- |
| Markdown, parsed natively | 95 documents, no service call |
| PDF / DOCX through `prebuilt-layout` | 25 documents, ~30 billable units, about $0.30 |
| Re-parses after the first | 0 — cached by content hash, model and API version |
| Local fallback | pymupdf + python-docx; raises on the 5 image-only PDFs rather than returning nothing |
```

Add a `docs/journal.md` entry matching the format already in that file — read the most recent entry and follow its heading style, date format and length.

- [ ] **Step 11: Commit**

```bash
git add src/fleet_copilot/ingest/ scripts/parse_corpus.py justfile README.md docs/journal.md tests/ingest/
git commit -m "feat(ingest): parse the whole corpus, each format by the right route

Markdown natively, PDF and DOCX through prebuilt-layout, both producing the same
ParsedDocument. CachedParser makes the second parse of the same bytes free,
which is what makes Plan 2's three-way chunking comparison meaningful: every
strategy reads byte-identical input rather than whatever the service returned
that run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Before Handing Back

1. `just check` — all three stages, output pasted, not asserted.
2. `uv run pre-commit run --all-files`.
3. `./infra/deploy.sh dev --what-if` reports no drift against what was deployed.
4. `just corpus-parse` reports `120 documents, 95 parsed natively, would analyse 0, 25 already cached`.
5. `just corpus-check` reports no drift: the manifest gained four keys and no document byte changed.
6. Re-read ADR 0005 and ADR 0006 and confirm nothing implemented contradicts them.
7. Confirm the scope held: **no chunker was written, `Chunk` was not touched, and no embedding call was made.** Those are Plans 2 and 3.

## What Plan 2 Inherits

Written down here so the next plan does not have to re-derive it:

- `ParsedDocument.content` is one Markdown string; `blocks` are non-overlapping, in reading order, and `content[start:end] == text` for every one. Chunkers slice `content` and never read `block.text`.
- `document.headings()` gives the breadcrumb material for the contextual-header strategy; `document.tables()` gives the blocks that become table chunks.
- `machine_types`, `item_numbers`, `revision` and `effective_date` come from `ManifestEntry`, not from the parsed document.
- Step-list atomicity is detected from Markdown ordered-list syntax in `content`. There is no `LIST_ITEM` role, deliberately — see the note above Task 1.
- `content_hash` hashes `embed_text`, not `text` (ADR 0006). Strategies 2 and 3 produce the same slice with different headers; hashing `text` would give them one embedding cache key between them.
- `LocalParser` is never wired into `run()`. It exists for tests and for a machine with no Azure access; a chunking comparison run against it would measure the fallback, not the pipeline.

## Known Gaps, Deliberately Left

- **No cache pruning.** A corpus regeneration changes every content hash and strands the old entries. ADR 0005 records this as harmless-but-accumulating. A `--prune` flag is a separate, small task if it is wanted.
- **`run()` discards what it parses.** It exists to populate the cache and prove the round trip; Plan 2 is what consumes `ParsedDocument`. Returning them before anything reads them would be guessing at the shape Plan 2 wants.
