# 2. Keyless access to every Azure data plane

Date: 2026-09-15

## Status

Accepted

## Context

This pipeline talks to four data planes: Azure OpenAI, Azure AI Search, Blob
Storage and Application Insights. Each one offers key-based authentication, and
each key is a bearer credential with no expiry, no per-caller identity and no
revocation short of rotating it.

Keys are also the default path in almost every quickstart, so they arrive by
accident: a connection string in an environment file, an API key in a notebook,
a storage key in a CI variable. Once one is in place the others follow, because
the code is already shaped around "read secret, pass secret".

The cost of avoiding them is not symmetric. Deciding this at the start costs a
handful of Bicep properties. Deciding it after the pipeline works means
rewriting every client construction site and rotating whatever has leaked.

## Decision

Key-based authentication is disabled at the resource level, so key-based code
fails rather than silently working:

- Storage: `allowSharedKeyAccess: false`
- Azure OpenAI: `disableLocalAuth: true` and a custom subdomain, without which
  token auth is not accepted
- Azure AI Search: `disableLocalAuth: true`
- Application Insights: `DisableLocalAuth: true`
- Key Vault: `enableRbacAuthorization: true` instead of access policies
- Container Apps: logs via `destination: 'azure-monitor'` and a diagnostic
  setting, rather than the `log-analytics` destination that requires the
  workspace shared key in the template

Access is granted instead by role assignment to one user-assigned managed
identity, in `modules/rbac.bicep`.

The application constructs `DefaultAzureCredential()` and nothing else. No
branch on environment: the class already reads `AZURE_CLIENT_ID` for its
managed-identity leg, which Container Apps sets and a laptop does not, falling
through to the `az login` session locally.

## Consequences

- The local and deployed code paths are the same path. A developer exercising
  the pipeline on a laptop is exercising production's auth flow.
- Disabling Application Insights local auth forces a fourth role assignment,
  **Monitoring Metrics Publisher**, beyond the three the brief listed. Without
  it the app cannot emit telemetry at all. This is the one place where the
  keyless decision expanded the RBAC surface.
- Role assignments are eventually consistent. Assignments carry
  `principalType: 'ServicePrincipal'` so deployment does not fail on a
  directory lookup for an identity created seconds earlier, but an app starting
  immediately after a first deploy may still see a few seconds of 403.
- Anyone running the pipeline locally needs the same roles on their own user
  principal. This is friction, and it is the intended kind: it makes access
  grants visible instead of shared.
- `grep -r "api_key" src/` returning nothing is an acceptance criterion, and
  `tests/test_no_key_based_auth.py` enforces it on every test run.
