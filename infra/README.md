# infra

Everything that creates cloud resources lives here, so the deployment topology
is reviewable in the same pull request as the behaviour it supports.

## What gets deployed

`main.bicep` is subscription-scoped: it creates the resource group and then one
module per concern.

| Module | Resource |
| --- | --- |
| `identity.bicep` | User-assigned managed identity — the only principal the app uses |
| `monitoring.bicep` | Log Analytics workspace + Application Insights |
| `storage.bicep` | Storage account with a `raw-docs` container |
| `openai.bicep` | Azure OpenAI with a `chat` and an `embeddings` deployment |
| `search.bicep` | Azure AI Search, Basic tier, semantic ranker enabled |
| `keyvault.bicep` | Key Vault, RBAC-authorised |
| `containerapps.bicep` | Container Apps managed environment |
| `rbac.bicep` | Every role assignment, in one place |

`budget.bicep` is deliberately separate: a spend guard that a `az group delete`
must not remove.

## Deploy

```console
$ az login
$ ./infra/deploy.sh dev --what-if   # preview
$ ./infra/deploy.sh dev             # apply
```

Re-running is a no-op. Resource names derive from `uniqueString(subscription().id,
environmentName)`, so they are stable for a given subscription and environment.

`deploy.sh` prints the environment variables the app needs on success. `ci` uses
the same template with smaller model capacity so both environments fit in one
subscription's quota.

## No keys anywhere

This is enforced by the infrastructure, not by convention:

- Storage has `allowSharedKeyAccess: false` — connection strings and
  account-key SAS simply do not work.
- Azure OpenAI and Azure AI Search have `disableLocalAuth: true`.
- Application Insights has `DisableLocalAuth: true`, so the instrumentation key
  is not sufficient to ingest telemetry.
- Key Vault uses `enableRbacAuthorization: true` rather than access policies.
- The Container Apps environment logs via `destination: 'azure-monitor'` and a
  diagnostic setting, which avoids passing it the workspace shared key.

The managed identity holds **Cognitive Services OpenAI User**, **Search Index
Data Contributor** and **Storage Blob Data Reader**, plus **Monitoring Metrics
Publisher** — the fourth is not in the original brief but is required once
Application Insights local auth is off, or the app cannot emit telemetry at all.

The application authenticates with `DefaultAzureCredential` and nothing else;
see `src/fleet_copilot/credentials.py`. `tests/test_no_key_based_auth.py` fails
the build if key-based auth reappears in `src/`.

## Region and model choices

Sweden Central, because it is the only nearby region that offers both models on
a SKU this subscription actually has quota for. The two models need *different*
SKUs, which is not obvious and is the single most likely thing to break on
another subscription:

| Model | SKU | Quota observed |
| --- | --- | --- |
| `gpt-4.1-mini` (2025-04-14) | `GlobalStandard` | 200 |
| `text-embedding-3-large` (1) | `Standard` | 350 |

`gpt-4o-mini` is *not* usable: new deployments of it were blocked from
2026-03-31, which preflight validation reports as `ServiceModelDeprecated`.

Check quota on a fresh subscription before deploying:

```console
$ az cognitiveservices usage list -l swedencentral -o table
```

## Deployment time

Measured on an empty subscription in Sweden Central:

| Run | Wall clock |
| --- | --- |
| First deploy, cold subscription | **16m 52s** |
| Second deploy, no changes | **80s** |

Azure AI Search is the entire difference: it took 16m 34s on its own, while
every other module finished inside 2m 08s. Provisioning the first Search
service in a region is slow and there is nothing the template can do about it —
it is already created in parallel with everything else.

If a sub-ten-minute cold deploy matters, the options are to keep one long-lived
Search service outside the per-environment resource group, or to accept that
the first deploy of the weekend is a coffee break and every later one is a
minute.

Note that Application Insights auto-creates a "Failure Anomalies" alert rule in
a nested deployment, which fails unless `Microsoft.AlertsManagement` is
registered. `deploy.sh` registers it; a portal-created deployment will show a
harmless failed nested deployment without it.

## Teardown

Run this at the end of the weekend:

```console
$ az group delete -n rg-fleet-copilot-dev --yes
```

Two things survive it on purpose:

- **The budget.** It is subscription-scoped, so it keeps watching. Remove it
  with `az consumption budget delete --budget-name budget-fleet-copilot`.
- **The soft-deleted Key Vault.** Retention is set to the 7-day minimum, but
  the name stays reserved until then, so a redeploy inside that window fails.
  Purge it first:

  ```console
  $ az keyvault purge --name "$(az keyvault list-deleted --query "[?contains(name,'kv-fleet-dev')].name" -o tsv)"
  ```

Azure OpenAI accounts are also soft-deleted; if a redeploy reports the name is
taken, purge with `az cognitiveservices account purge`.
