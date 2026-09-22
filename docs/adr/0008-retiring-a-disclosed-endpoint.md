# 8. Retiring a disclosed endpoint, without renaming the environment

Date: 2026-09-22

## Status

Accepted

## Context

A planning document committed to this public repository carried the deployed
Document Intelligence endpoint as an example value — the real hostname, labelled
"e.g.". It had been pushed and was public.

An endpoint hostname is not a credential. Nothing authenticates with one, and
every data plane here is keyless (ADR 0002), so possession of the hostname grants
nothing on its own. It is still worth removing, for the same reason ADR 0002's
predecessor commit removed the subscription and tenant ids: a hostname plus a
resource group name plus a tenant is a complete targeting profile for a phishing
or illicit-consent attempt, and it names a specific deployment rather than a
class of them.

Redacting the working tree does not help. The value was in history and pushed, so
the fix had to be a history rewrite and a force-push — and a force-push does not
remove anything from GitHub. Old commits stay reachable by SHA until GitHub
garbage-collects, and survive in any fork or clone taken before the rewrite.

That is the part that forces a decision. **Once a hostname has been published it
cannot be unpublished, only retired.** The redaction stops it spreading further;
it does not undo the disclosure. The only action that makes the disclosed value
stop meaning anything is destroying the resource it names.

Every name in `main.bicep` is derived from `uniqueString(subscription().id,
environmentName)`. That is deliberate — it keeps a redeploy idempotent — but it
also means deleting the account and redeploying hands it the same name straight
back. Retiring a name required changing how that one name is derived.

The alternatives were worse. Deploying under a new `environmentName` renames
every resource in the environment and abandons the storage account, the search
index and the OpenAI deployments along with it, to retire one hostname. Adding a
global salt does the same thing one step less obviously. Hand-creating a
replacement outside Bicep leaves the template describing an account that no
longer exists, and the next `deploy.sh` run recreates the old name.

## Decision

**The Document Intelligence account name carries its own rotation salt.**

```bicep
param documentIntelligenceNameSalt string = 'r2'
var documentIntelligenceSuffix = uniqueString(
  subscription().id, environmentName, documentIntelligenceNameSalt
)
```

Bumping the salt produces a new deterministic name. A redeploy at the same salt
is still a no-op, so idempotency is preserved. `r1` denotes the original,
unsalted two-argument form; the current deployment is `r2`.

No other resource is salted. This one is salted because its endpoint was
published, and the parameter exists so the next disclosure is a one-line change
rather than an environment rebuild.

The old account was deleted, not merely renamed in the template. Bicep deploys
incrementally and would otherwise have left it running under the published
hostname indefinitely.

## Consequences

**The disclosed hostname no longer resolves.** That is the only outcome here that
the redaction itself could not deliver.

**The layout cache survived.** Its key is `(source_sha256, model_id,
api_version)` (ADR 0005) and does not include the endpoint, so all 25 cached
layouts stayed valid across the swap: `just corpus-parse` reports *"would analyse
0, 25 already cached"*, and re-analysis cost nothing. A cache keyed by endpoint
would have turned an endpoint rotation into a re-analysis bill, which is an
argument for that key that was not obvious when it was chosen.

**Rotation is now cheap, so there is no reason to tolerate a disclosure.** One
parameter, one deploy, one delete.

**A rotation is not free of coordination.** Anything holding the old endpoint —
a local `.env`, a Container App revision, a colleague's shell — breaks at the
moment of deletion rather than degrading. `deploy.sh` prints the new export
lines, which is the intended way to pick it up.

**The old commits are still on GitHub.** The rewrite and force-push stop the
value spreading; they do not retract it. Asking GitHub Support to garbage-collect
the unreachable objects is the remaining step, and it is a request, not a
guarantee.

**The real lesson is upstream of all of this.** The value entered the repository
as an illustrative example in a planning document, where nothing treats it as
sensitive and no reviewer is looking for it. Example endpoints in prose should be
written as placeholders from the start — `di-<workload>-<env>-<suffix>` — because
the cost of a real one is this entire procedure.
