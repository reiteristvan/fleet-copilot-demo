# Document Intelligence Parsing and the Layout Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ingest` a parser that turns the corpus's PDF and DOCX files into one Markdown string with role-tagged spans over it, backed by a content-addressed cache, so the four chunking strategies of the next story all read byte-identical input.

**Architecture:** A `DocumentParser` Protocol with two implementations — `AzureLayoutParser` (Azure AI Document Intelligence, `prebuilt-layout`, Markdown output) and `LocalParser` (pymupdf for PDF, python-docx for DOCX) which raises rather than degrading on a file it cannot honestly read. A `LayoutCache` Protocol with a blob-backed and a directory-backed implementation stores the *raw* `AnalyzeResult` JSON keyed by content hash, so re-interpreting a layout is free and re-analysing is the only thing that costs. Every module holding a decision is pure and importable without the Azure SDK; only the `run`-shaped functions touch the network.

**Tech Stack:** Python 3.12, pydantic v2 (frozen, `extra="forbid"`), `azure-ai-documentintelligence` 1.0.x (aio client), `azure-identity` (aio credential), `pymupdf`, `python-docx`, `azure-storage-blob`, Bicep, pytest + pytest-asyncio (strict mode), mypy --strict, ruff.

**Spec:** `docs/adr/0005-document-parsing-and-the-layout-cache.md`

## Global Constraints

Copied from `CLAUDE.md` and ADR 0005. Every task's requirements implicitly include this section.

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
- **Comment only what the code cannot say itself** — a constraint, a rejected alternative, a non-obvious failure prevented, an external fact. A comment restating the line below it will be removed in review.
- **Never introduce key-based auth to an Azure data plane.** Use `get_credential()` / `get_async_credential()` from `fleet_copilot.credentials`. `tests/test_no_key_based_auth.py` enforces this.
- **Pin Azure API versions to values already checked for this work:** ARM `2026-03-01` for `Microsoft.CognitiveServices/accounts`, `2025-08-01` for storage, `2024-08-01` for budgets, `2022-04-01` for role assignments; data-plane `2024-11-30` (the SDK default, v4.0 GA).
- **Commits are atomic and semantic** (`feat(ingest): …`, `build: …`, `ci: …`). One concern per commit; the body says *why*.
- **Subscription:** `00000000-0000-0000-0000-000000000000` ("Azure subscription 1"), tenant `00000000-0000-0000-0000-000000000000`, region `swedencentral`, resource group `rg-fleet-copilot-dev`.
- **Role definition id for Cognitive Services User:** `a97b65f3-24c7-4388-baec-2e87135dc908` (verified with `az role definition list`).
- **Spans are `unicodeCodePoint`**, never the SDK's `textElements` default, and `ParsedBlock.text` is always `content[start:end]` — derived, never copied from `paragraph.content`.

## File Structure

| File | Responsibility |
| --- | --- |
| `infra/modules/docintel.bicep` (new) | The Document Intelligence account. Nothing else. |
| `infra/modules/storage.bicep` (modify) | Gains the `layout-cache` container beside `raw-docs`. |
| `infra/modules/rbac.bicep` (modify) | Gains the Cognitive Services User assignment. |
| `infra/main.bicep` (modify) | Wires the module in; outputs the endpoint and cache container. |
| `infra/budget.bicep` (modify) | Gains the second, resource-filtered $15 budget. |
| `infra/deploy.sh` (modify) | Prints the two new environment variables. |
| `src/fleet_copilot/ingest/parse.py` (new) | The contract: `BlockRole`, `ParsedBlock`, `ParsedPage`, `ParsedDocument`, `DocumentParser`, `ParseError`. Pure; no SDK import. |
| `src/fleet_copilot/ingest/layout.py` (new) | `layout_from_analyze_result()` (pure) and `AzureLayoutParser` (network, lazy import). |
| `src/fleet_copilot/ingest/fallback.py` (new) | `LocalParser`. Raises on anything it cannot honestly read. |
| `src/fleet_copilot/ingest/cache.py` (new) | `cache_key()`, `LayoutCache`, `LocalLayoutCache`, `BlobLayoutCache`. |
| `src/fleet_copilot/ingest/run.py` (new) | `CachedParser`, `ParseReport`, `run()`. The only module that ties the others together. |
| `src/fleet_copilot/ingest/models.py` (modify) | `Chunk.context_prefix` and `Chunk.embed_text`. |
| `src/fleet_copilot/credentials.py` (modify) | `get_async_credential()`. |
| `src/fleet_copilot/config.py` (modify) | Three new settings. |
| `scripts/parse_corpus.py` (new) | Argument-parsing shim only, matching `upload_corpus.py`. |
| `scripts/capture_layout_fixtures.py` (new) | One-shot fixture capture, committed so it is repeatable. |
| `tests/ingest/fixtures/layout/*.json` (new) | Three real `AnalyzeResult` payloads, captured once. |

Task order is dependency order. Tasks 3–8 need no Azure account and can be reviewed independently of Tasks 1–2.

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
Expected: exactly three creates — the `Microsoft.CognitiveServices/accounts` account, the `layout-cache` container, the role assignment. **No modifies and no deletes.** A modify on the storage account or the OpenAI account means this task changed a property it should not have; stop and diff before continuing.

- [ ] **Step 9: Document it in `infra/README.md`**

Add a row to the "What gets deployed" table (`infra/README.md:11-20`), after the `search.bicep` row:

```markdown
| `docintel.bicep` | Azure AI Document Intelligence, S0, for `prebuilt-layout` |
```

Change the `storage.bicep` row to name both containers:

```markdown
| `storage.bicep` | Storage account with a `raw-docs` and a `layout-cache` container |
```

Extend the teardown note at line 118 — it currently reads `az consumption budget delete --budget-name budget-fleet-copilot` — to name the second budget too:

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

**Depends on Task 3** for `get_async_credential`. Do Task 3 first, or do Steps 1–4 here, then Task 3, then return for Steps 5–10.

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups].dev` only), `uv.lock` (via `uv`), `.env.example`, `src/fleet_copilot/config.py`
- Create: `scripts/capture_layout_fixtures.py`, `tests/ingest/fixtures/layout/*.json`
- Test: `tests/ingest/test_fixtures.py`

**Interfaces:**
- Consumes: Task 1's `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`; Task 3's `get_async_credential`.
- Produces: `Settings.azure_document_intelligence_endpoint: str | None`, `Settings.azure_document_intelligence_api_version: str = "2024-11-30"`, `Settings.azure_layout_cache_container: str = "layout-cache"`, and three fixture files at `tests/ingest/fixtures/layout/<stem>.json`, each the `as_dict()` of a real `AnalyzeResult`. Tasks 6 and 9 read these.

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

Committed rather than throwaway, so the fixtures can be regenerated when the API version moves.

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

If any count is zero, stop. A zero-length result from `prebuilt-layout` means the request succeeded and returned nothing, which is the failure mode F0 produces and S0 should not.

- [ ] **Step 7: Write the fixture guard test**

Create `tests/ingest/test_fixtures.py`:

```python
"""Assert the committed fixtures are what the rest of the ingest tests assume.

These three files stand in for the Document Intelligence service everywhere
else in the suite. If one is truncated, re-captured against a different API
version, or committed empty, every test reading it would go on passing against
a weaker document than it was written for.
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


def test_the_scanned_fixture_proves_ocr_ran() -> None:
    """The scanned PDF has no text layer, so any content at all came from OCR.

    This is the fixture that makes the Azure path worth paying for. If it comes
    back empty, the local fallback and the service are indistinguishable, and
    the comparison the next story runs would be measuring nothing.
    """
    payload = json.loads(
        (FIXTURES / "service-report-sd50b-2026-10142-17.json").read_text(encoding="utf-8")
    )

    assert len(payload["content"]) > 200
```

- [ ] **Step 8: Run the fixture tests**

Run: `uv run pytest tests/ingest/test_fixtures.py -v`
Expected: 4 passed.

- [ ] **Step 9: Check the fixture sizes before committing**

```bash
ls -la tests/ingest/fixtures/layout/
```

The pre-commit `check for added large files` hook rejects anything over its threshold. OCR emits a word entry with a polygon per word, so the scanned fixture is the large one. If it trips the hook, do **not** raise the hook's limit — that is weakening a gate. Store that one gzipped and decompress it in `fixture()` instead, and say so in a comment.

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
- Produces: `get_async_credential() -> azure.identity.aio.DefaultAzureCredential`. Tasks 2 and 9 use it.

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

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/test_credentials.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/credentials.py tests/test_credentials.py
git commit -m "feat(credentials): add the async credential the aio clients need

A separate function rather than a branch on environment. An aio client handed
the synchronous credential accepts it and fails on the first request, inside a
poller, where the traceback points at the SDK rather than at the credential.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `Chunk.context_prefix` and `Chunk.embed_text`

Pure model change, no parser involvement. ADR 0005 explains why it lands now rather than with the chunkers: retrieval binds to `embed_text`, and adding it afterwards is a reindex.

**Files:**
- Modify: `src/fleet_copilot/ingest/models.py:60-100`
- Test: `tests/ingest/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Chunk.context_prefix: str | None = None` and the computed `Chunk.embed_text: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_models.py` (add `from pydantic import ValidationError` to its imports if absent):

```python
def test_embed_text_is_the_text_when_there_is_no_prefix() -> None:
    chunk = Chunk(doc_id="d", ordinal=0, text="abc", start=0, end=3)

    assert chunk.embed_text == "abc"


def test_embed_text_joins_the_prefix_without_disturbing_the_span() -> None:
    chunk = Chunk(
        doc_id="sdm-43-service-manual",
        ordinal=14,
        text="The emergency stop is not an isolator.",
        start=1794,
        end=1832,
        context_prefix="Single-disc machine SDM-43 - service manual > Safety",
    )

    assert chunk.embed_text == (
        "Single-disc machine SDM-43 - service manual > Safety\n\n"
        "The emergency stop is not an isolator."
    )
    # The span still describes text alone. That is the whole point of the field.
    assert chunk.end - chunk.start == len(chunk.text)


def test_a_blank_prefix_is_rejected() -> None:
    """A blank prefix is not the same as no prefix, and reads as one.

    It produces embed_text with two leading newlines, embedding one chunk
    slightly differently from its unprefixed neighbours -- exactly the silent
    skew an A/B between chunking strategies cannot survive.
    """
    with pytest.raises(ValidationError):
        Chunk.model_validate(
            {
                "doc_id": "d",
                "ordinal": 0,
                "text": "abc",
                "start": 0,
                "end": 3,
                "context_prefix": "  ",
            }
        )
```

Note `end=1832`: the sentence is 38 characters. Count it rather than trusting this line — the validator will reject a mismatch, which is the test working.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/ingest/test_models.py -v -k "embed_text or blank_prefix"`
Expected: FAIL — `extra fields not permitted` on `context_prefix`, and `AttributeError` on `embed_text`.

- [ ] **Step 3: Implement**

In `src/fleet_copilot/ingest/models.py`, add the field to `Chunk` after `end`:

```python
    context_prefix: NonEmptyStr | None = None
    """Headings above this chunk, joined into a breadcrumb.

    Outside the span on purpose. A chunk's subject often appears only in the
    heading above it, which is not in its own text; embedding the breadcrumb
    recovers that without moving start/end, so a citation still highlights the
    source exactly.
    """
```

`Chunk`'s `model_config` does not set `str_strip_whitespace`, so `min_length=1` alone would admit `"  "`. Add the validator:

```python
    @field_validator("context_prefix")
    @classmethod
    def _reject_a_blank_prefix(cls, value: str | None) -> str | None:
        """Treat a whitespace-only prefix as the error it is, not as no prefix.

        A blank prefix produces embed_text with two leading newlines, embedding
        one chunk slightly differently from its unprefixed neighbours -- the
        kind of skew that makes a chunking A/B measure the wrong thing.
        """
        if value is not None and not value.strip():
            msg = "context_prefix must not be blank; omit it instead"
            raise ValueError(msg)
        return value
```

and the computed field after `chunk_id`:

```python
    @computed_field  # type: ignore[prop-decorator]  # mypy: decorators over @property
    @property
    def embed_text(self) -> str:
        """What the retriever embeds, as opposed to what a citation quotes."""
        if self.context_prefix is None:
            return self.text
        return f"{self.context_prefix}\n\n{self.text}"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_models.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_copilot/ingest/models.py tests/ingest/test_models.py
git commit -m "feat(ingest): separate what a chunk embeds from what it cites

A chunk's subject often lives only in the heading above it, outside its own
span. context_prefix carries the breadcrumb and embed_text joins it; text and
start/end stay verbatim, so the validator keeping citations honest is untouched.

Lands now rather than with the chunkers because retrieval binds to embed_text,
and adding it afterwards is a reindex.

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
  - `class ParserId(StrEnum)`: `AZURE_LAYOUT = "azure-document-intelligence"`, `LOCAL = "local"`
  - `class BlockRole(StrEnum)`: `TITLE`, `SECTION_HEADING`, `PARAGRAPH`, `PAGE_HEADER`, `PAGE_FOOTER`, `PAGE_NUMBER`, `FOOTNOTE`, `FORMULA_BLOCK`, `TABLE`
  - `HEADING_ROLES: frozenset[BlockRole]`
  - `class ParsedPage(BaseModel)`: `page_number: int`, `start: int`, `end: int`
  - `class ParsedBlock(BaseModel)`: `role: BlockRole`, `text: str`, `start: int`, `end: int`, `page_number: int`
  - `class ParsedDocument(BaseModel)`: `doc_id`, `source_sha256`, `parser`, `model_id`, `api_version`, `content`, `blocks`, `pages`, `parsed_at`; method `headings() -> tuple[ParsedBlock, ...]`
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
        "parser": ParserId.LOCAL,
        "model_id": "local",
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
    """Chunk ordinals come from block order, and an ordinal ordered wrongly is wrong."""
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
    document = a_document()

    assert tuple(block.role for block in document.headings()) == (BlockRole.TITLE,)


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
text. Four chunking strategies will slice this, and they slice one coordinate
system rather than each re-deriving offsets and getting it differently wrong.

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
    LOCAL = "local"


class BlockRole(StrEnum):
    """What a span of content is.

    The first eight mirror Document Intelligence's paragraph roles one for one,
    including the three kinds of page furniture. Those are kept rather than
    stripped: removing them would shift every later offset, and a chunker that
    wants to skip them can filter on the role.
    """

    TITLE = "title"
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    FORMULA_BLOCK = "formula_block"
    TABLE = "table"


HEADING_ROLES: frozenset[BlockRole] = frozenset({BlockRole.TITLE, BlockRole.SECTION_HEADING})
"""Roles a breadcrumb is built from. The layout-aware chunker reads this."""


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

        A parse may run on a laptop in one timezone and in CI in another; a
        naive timestamp cannot be ordered against one from the other without
        guessing its offset.
        """
        if value.tzinfo is None:
            msg = "parsed_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _blocks_are_ordered_slices_of_content(self) -> Self:
        """Check every block against the content it claims to describe.

        Two failures, both invisible downstream. A block whose text is not what
        its span covers mis-slices every chunk built from it. Blocks out of
        reading order give chunk ordinals that scramble a multi-chunk answer
        while every individual citation still looks correct.
        """
        previous_start = -1
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
            previous_start = block.start
        return self

    def headings(self) -> tuple[ParsedBlock, ...]:
        """Every title and section heading, in reading order."""
        return tuple(block for block in self.blocks if block.role in HEADING_ROLES)


@runtime_checkable
class DocumentParser(Protocol):
    """Turns bytes into a :class:`ParsedDocument`.

    ``parse`` is async because the Azure implementation is a network call and
    the local one reads files; neither may hold the event loop. A parser that
    cannot read what it was given raises :class:`ParseError` rather than
    returning an empty document -- an empty document is indistinguishable from
    a blank page, and this corpus contains neither.
    """

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument: ...
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_parse.py -v`
Expected: 6 passed.

- [ ] **Step 5: Type-check, because the Protocol is the point**

Run: `uv run mypy`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/parse.py tests/ingest/test_parse.py
git commit -m "feat(ingest): define what a parsed document is

One content string, blocks as spans into it, nothing else holding text. Four
chunking strategies are queued to run over this; sharing one coordinate system
is what lets Chunk's span invariant hold by construction rather than be
re-derived, differently, four times.

Page headers and footers are tagged rather than stripped: stripping shifts
every later offset, and a chunker that wants to skip them can filter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The Document Intelligence mapper

The pure half of the Azure parser: `AnalyzeResult` JSON in, `ParsedDocument` out. Tested entirely against Task 2's fixtures — no network, no account.

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
from fleet_copilot.ingest.parse import BlockRole, ParseError, ParserId

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
STEMS = (
    "sdm-43-service-manual",
    "service-report-sd50b-2026-10142-17",
    "sdr-90-operator-manual-hu",
)


def fixture(stem: str) -> Mapping[str, Any]:
    payload: Mapping[str, Any] = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
    return payload


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_is_a_verbatim_slice_of_content(stem: str) -> None:
    """The invariant the whole design rests on, checked against real output.

    ParsedDocument validates this itself, so a mapper that took text from
    paragraph.content rather than from the span fails here rather than produce
    chunks quoting something the document does not say.
    """
    document = layout_from_analyze_result(fixture(stem), doc_id=stem, source_sha256="a" * 64)

    for block in document.blocks:
        assert document.content[block.start : block.end] == block.text


@pytest.mark.parametrize("stem", STEMS)
def test_the_parser_and_model_are_recorded(stem: str) -> None:
    document = layout_from_analyze_result(fixture(stem), doc_id=stem, source_sha256="a" * 64)

    assert document.parser is ParserId.AZURE_LAYOUT
    assert document.model_id == "prebuilt-layout"
    assert document.api_version == "2024-11-30"


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_carries_a_known_role(stem: str) -> None:
    """Page furniture is tagged, not dropped.

    Dropping it would shift every offset after it, and no span could then be
    checked against the cached JSON.
    """
    document = layout_from_analyze_result(fixture(stem), doc_id=stem, source_sha256="a" * 64)

    assert document.blocks
    for block in document.blocks:
        assert block.role in set(BlockRole)


def test_the_service_manual_yields_headings() -> None:
    """Headings are what three of the four chunking strategies split on."""
    document = layout_from_analyze_result(
        fixture("sdm-43-service-manual"), doc_id="sdm-43-service-manual", source_sha256="a" * 64
    )

    headings = document.headings()
    assert headings, "prebuilt-layout returned no headings for a document that has six"
    assert any("Safety" in block.text for block in headings)


def test_the_scanned_report_yields_blocks() -> None:
    """An image-only PDF. Any block at all came out of OCR."""
    document = layout_from_analyze_result(
        fixture("service-report-sd50b-2026-10142-17"),
        doc_id="service-report-sd50b-2026-10142-17",
        source_sha256="a" * 64,
    )

    assert document.blocks


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

    blocks: list[ParsedBlock] = []
    for paragraph in payload.get("paragraphs") or []:
        spans = paragraph.get("spans") or []
        if not spans:
            continue
        start, end = _span_bounds(spans)
        text = content[start:end]
        if not text:
            continue
        regions = paragraph.get("boundingRegions") or [{"pageNumber": 1}]
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
                page_number=int(regions[0]["pageNumber"]),
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

Task 9 adds `AzureLayoutParser` to this same module and will need `import hashlib` at the top then. Do not add it now — ruff flags an unused import and `just lint` would fail.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/ingest/test_layout.py -v`
Expected: all pass.

If `test_every_block_is_a_verbatim_slice_of_content` fails, the mapper is at fault, not the test — that assertion *is* the contract. The likeliest cause is a paragraph with non-contiguous spans; the outer-bounds approach exists precisely to survive that, so investigate before touching the assertion.

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/layout.py tests/ingest/test_layout.py
git commit -m "feat(ingest): map Document Intelligence layout onto ParsedDocument

Block text is derived from the span rather than copied from paragraph.content:
in Markdown mode the service returns undecorated text there, which does not
always coincide with what the span covers. Deriving makes the span invariant
true by construction, and the tests assert it against real service output.

A successful call that returned no content raises rather than caching an empty
document -- that is the F0 failure mode, and cached, every chunker would read
it as a blank page forever.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The local fallback

**Files:**
- Create: `src/fleet_copilot/ingest/fallback.py`
- Test: `tests/ingest/test_fallback.py`

**Interfaces:**
- Consumes: Task 5's contract.
- Produces: `class LocalParser` implementing `DocumentParser`, with `async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument`.

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
    planted prompt injection. A fallback that returned an empty document for
    them would keep the suite green while the OCR path went untested.
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
async def test_it_produces_no_headings() -> None:
    """Not a silent limitation. A chunking comparison run against this parser
    would measure the fallback rather than the pipeline, and this says so."""
    document = await LocalParser().parse(
        (PUBLISHED / TEXT_PDF).read_bytes(), doc_id="sdm-43-service-manual", content_type=PDF_TYPE
    )

    assert document.headings() == ()


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

This is what unit tests and Markdown-only runs use, so CI never calls the
service. It is deliberately a weaker parser, not an equivalent one: it produces
no heading roles and no table structure, and it raises on a PDF with no text
layer rather than returning an empty document.

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
        page_bounds[page_number] = (
            cursor if known is None else known[0],
            cursor + len(text),
        )
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
Expected: clean. If `pymupdf` has no stubs, mypy --strict will object to the untyped import; resolve it with a targeted `# type: ignore[import-untyped]` carrying that code and a reason naming pymupdf, **not** by adding a global mypy override.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_copilot/ingest/fallback.py tests/ingest/test_fallback.py
git commit -m "feat(ingest): add the offline parser, which refuses to fake OCR

Deliberately weaker than the service and explicit about it: no heading roles,
no tables, and a ParseError on a PDF with no text layer. Five of the twenty
PDFs here are image-only and one hides a planted injection; a fallback that
returned an empty document for them would keep the suite green while the OCR
path went untested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The layout cache

**Files:**
- Create: `src/fleet_copilot/ingest/cache.py`
- Test: `tests/ingest/test_cache.py`

**Interfaces:**
- Consumes: `get_credential` from `fleet_copilot.credentials`.
- Produces:
  - `cache_key(*, source_sha256: str, model_id: str, api_version: str) -> str` returning `f"{model_id}/{api_version}/{source_sha256}.json"`
  - `class LayoutCache(Protocol)`: `async def get(self, key: str) -> Mapping[str, Any] | None`, `async def put(self, key: str, payload: Mapping[str, Any]) -> None`
  - `class LocalLayoutCache(root: Path)` and `class BlobLayoutCache(endpoint: str, container: str)`

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
    cache = LocalLayoutCache(tmp_path)

    assert await cache.get("prebuilt-layout/2024-11-30/deadbeef.json") is None


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

    The SDK is imported per call rather than at module scope so this module
    stays importable without azure-storage-blob, which is a dev-only dependency.
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

Stores the raw AnalyzeResult, not our model of it: re-interpreting a layout
must be free while re-analysing is the thing that costs, and caching the parsed
model would invert that.

Model id and API version are in the key, so moving to a different model or
version invalidates by construction rather than silently serving output whose
missing headings look like a document that has none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The Azure parser, the run script, and the docs

The thin network layer, and the command that puts it all together.

**Files:**
- Modify: `src/fleet_copilot/ingest/layout.py`, `justfile`, `README.md`, `docs/journal.md`
- Create: `src/fleet_copilot/ingest/run.py`, `scripts/parse_corpus.py`
- Test: `tests/ingest/test_run.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `class AzureLayoutParser(endpoint: str, *, api_version: str)` with `async def analyse(self, data: bytes) -> Mapping[str, Any]` and `async def parse(...)`; `class Analyser(Protocol)`; `class CachedParser(analyser: Analyser, cache: LayoutCache, *, api_version: str)`; `class ParseReport`; `async def run(manifest, corpus_root, settings, *, dry_run: bool = True) -> ParseReport`.

- [ ] **Step 1: Add `AzureLayoutParser` to `src/fleet_copilot/ingest/layout.py`**

Append to the module written in Task 6:

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
        DocumentParser requires it and the local parser genuinely needs it.
        """
        payload = await self.analyse(data)
        return layout_from_analyze_result(
            payload, doc_id=doc_id, source_sha256=hashlib.sha256(data).hexdigest()
        )
```

- [ ] **Step 2: Write the failing test for the cache-aware wrapper**

Create `tests/ingest/test_run.py`:

```python
"""The wrapper that makes a second parse of the same bytes free."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.ingest.cache import LocalLayoutCache, cache_key
from fleet_copilot.ingest.parse import ParsedDocument
from fleet_copilot.ingest.run import CachedParser

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

    Four chunking strategies will read these documents. A comparison is only
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
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/ingest/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_copilot.ingest.run'`

- [ ] **Step 4: Implement `src/fleet_copilot/ingest/run.py`**

```python
"""Parse the corpus once, and never twice for the same bytes.

Split the way corpus/upload.py is split: :class:`CachedParser` holds the
decision and is tested against a fake analyser, and only :func:`run` reaches
outside the repository.
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
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.corpus.upload import content_type_for
from fleet_copilot.ingest.cache import BlobLayoutCache, LayoutCache, cache_key
from fleet_copilot.ingest.layout import MODEL_ID, AzureLayoutParser, layout_from_analyze_result
from fleet_copilot.ingest.parse import ParsedDocument


class Analyser(Protocol):
    """Anything that can turn bytes into a raw AnalyzeResult payload."""

    async def analyse(self, data: bytes) -> Mapping[str, Any]: ...


class CachedParser:
    """An analyser with a cache in front of it. Implements DocumentParser."""

    def __init__(self, analyser: Analyser, cache: LayoutCache, *, api_version: str) -> None:
        self._analyser = analyser
        self._cache = cache
        self._api_version = api_version

    async def parse(self, data: bytes, *, doc_id: str, content_type: str) -> ParsedDocument:
        """Return the parsed document, analysing only on a cache miss.

        ``content_type`` is unused: the service sniffs the format itself. It is
        in the signature because DocumentParser requires it.
        """
        source_sha256 = hashlib.sha256(data).hexdigest()
        key = cache_key(
            source_sha256=source_sha256, model_id=MODEL_ID, api_version=self._api_version
        )

        payload = await self._cache.get(key)
        if payload is None:
            payload = await self._analyser.analyse(data)
            await self._cache.put(key, payload)

        return layout_from_analyze_result(payload, doc_id=doc_id, source_sha256=source_sha256)


class ParseReport(BaseModel):
    """What a parse run did, or would have done."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    planned: int
    analysed: int
    cached: int
    dry_run: bool

    def describe(self) -> str:
        """One ASCII line, safe for a cp1252 Windows console."""
        verb = "would analyse" if self.dry_run else "analysed"
        return f"{self.planned} documents, {verb} {self.analysed}, {self.cached} already cached"


def parse_targets(manifest: Manifest) -> tuple[tuple[str, str, str], ...]:
    """Return ``(doc_id, path, content_type)`` for every document worth analysing.

    Markdown is excluded: it has no layout to recover, and sending it would pay
    per page for an OCR of text we already hold.
    """
    return tuple(
        (entry.doc_id, entry.path, content_type_for(Path(entry.path).suffix))
        for entry in manifest.documents
        if entry.is_converted
    )


def endpoint_and_container(settings: Settings) -> tuple[str, str]:
    """Return the endpoint and cache container, or explain what is missing."""
    if not settings.azure_document_intelligence_endpoint:
        msg = (
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is not set. Populate it from "
            "`./infra/deploy.sh dev`, which prints it as an export line."
        )
        raise CorpusDataError(msg)
    if not settings.azure_storage_blob_endpoint:
        msg = (
            "AZURE_STORAGE_BLOB_ENDPOINT is not set. Populate it from "
            "`./infra/deploy.sh dev`, which prints it as an export line."
        )
        raise CorpusDataError(msg)
    return (
        settings.azure_document_intelligence_endpoint,
        settings.azure_layout_cache_container,
    )


async def run(
    manifest: Manifest,
    corpus_root: Path,
    settings: Settings,
    *,
    dry_run: bool = True,
) -> ParseReport:
    """Parse every converted document, or report what a parse would do.

    ``dry_run`` defaults to true because this is the part that reaches outside
    the repository and spends money; the default should be the one that cannot
    surprise anybody.
    """
    endpoint, container = endpoint_and_container(settings)
    targets = parse_targets(manifest)
    api_version = settings.azure_document_intelligence_api_version
    blob_endpoint = settings.azure_storage_blob_endpoint or ""
    cache = BlobLayoutCache(blob_endpoint, container)

    analysed = 0
    cached = 0
    parser = CachedParser(
        AzureLayoutParser(endpoint, api_version=api_version), cache, api_version=api_version
    )

    for doc_id, path, content_type in targets:
        data = await asyncio.to_thread((corpus_root / path).read_bytes)
        key = cache_key(
            source_sha256=hashlib.sha256(data).hexdigest(),
            model_id=MODEL_ID,
            api_version=api_version,
        )
        if await cache.get(key) is not None:
            cached += 1
            continue
        if dry_run:
            analysed += 1
            continue
        await parser.parse(data, doc_id=doc_id, content_type=content_type)
        analysed += 1

    return ParseReport(planned=len(targets), analysed=analysed, cached=cached, dry_run=dry_run)
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `uv run pytest tests/ingest/ -v`
Expected: all pass.

- [ ] **Step 6: Write `scripts/parse_corpus.py`**

A shim with no logic, mirroring `scripts/upload_corpus.py`. The one difference is `asyncio.run`: `corpus.upload.run` is synchronous and `ingest.run.run` is a coroutine.

```python
"""Parse the published corpus through Azure AI Document Intelligence.

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

Expected: the first reports 25 documents, 25 to analyse, 0 cached; the second analyses them; the third reports 25 documents, 0 to analyse, 25 cached. That third line is the deliverable of this whole story.

- [ ] **Step 9: Run the full gate**

Run: `just check`
Then: `uv run pre-commit run --all-files`
Expected: both clean. Paste the output; do not assert it passed.

- [ ] **Step 10: Update the docs**

Add a table to `README.md` after the corpus counts table (`README.md:58-63`), in the same style:

```markdown
| Parsing | |
| --- | --- |
| Analysed through `prebuilt-layout` | 25 of 120 documents; the other 95 are Markdown and have no layout |
| Billable units per full parse | ~30, about $0.30 |
| Re-parses after the first | 0 — cached by content hash, model and API version |
| Local fallback | pymupdf + python-docx; raises on the 5 image-only PDFs rather than returning nothing |
```

Add a `docs/journal.md` entry matching the format already in that file — read the most recent entry and follow its heading style, date format and length.

- [ ] **Step 11: Commit**

```bash
git add src/fleet_copilot/ingest/ scripts/parse_corpus.py justfile README.md docs/journal.md tests/ingest/
git commit -m "feat(ingest): parse the corpus through Document Intelligence, once

CachedParser makes the second parse of the same bytes free, which is what makes
the next story's four-way chunking comparison meaningful: every strategy reads
byte-identical input rather than whatever the service returned that run.

Markdown documents are excluded from the parse -- they have no layout to
recover, and sending them would pay per page to OCR text we already hold.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Verification Before Handing Back

1. `just check` — all three stages, output pasted, not asserted.
2. `uv run pre-commit run --all-files`.
3. `./infra/deploy.sh dev --what-if` reports no drift against what was deployed.
4. `just corpus-parse` reports 25 documents, 0 to analyse, 25 cached.
5. Re-read ADR 0005 and confirm nothing implemented contradicts it.
6. Confirm the scope held: **no chunker was written.** `ingest/` gained a parser and a cache, and nothing that splits a document. An empty package is empty on purpose.

## Known Gaps, Deliberately Left

- **No cache pruning.** A corpus regeneration changes every content hash and strands the old entries. ADR 0005 records this as harmless-but-accumulating. A `--prune` flag is a separate, small task if it is wanted.
- **Tables are tagged, not modelled.** `BlockRole.TABLE` exists but the mapper does not emit it; `AnalyzeResult.tables` stays in the cached payload for the layout-aware chunker to read next story. Modelling table structure before a chunker needs it would be guessing at its shape.
- **The corpus's 95 Markdown documents never reach a parser.** They have no layout to analyse. Whether they get a trivial `ParsedDocument` or bypass parsing entirely is a chunking-story decision.
- **`LocalParser` is never wired into `run()`.** It exists for the tests and for a Markdown-only run; choosing between the two parsers at runtime is a decision the chunking story will actually have to make.
