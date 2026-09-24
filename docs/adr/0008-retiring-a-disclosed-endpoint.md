# 8. Retiring a disclosed endpoint, without renaming the environment

Date: 2026-09-22

## Status

Accepted

## Context

A planning document in this public repository carried the deployed Document
Intelligence endpoint as an example value. It was the real hostname, labelled
"e.g.". It had been pushed and was public.

An endpoint hostname is not a credential. Nothing authenticates with one, and
every data plane here is keyless (ADR 0002). Possession of the hostname grants
nothing.

It is still worth removing. A hostname, a resource group name and a tenant
together make a targeting profile for a phishing or illicit-consent attempt. The
value names one specific deployment rather than a class of them. The commit that
removed the subscription and tenant ids used the same reasoning.

Redacting the working tree does not help. The value was in history and pushed, so
the fix needed a history rewrite and a force-push. A force-push removes nothing
from GitHub. Old commits stay reachable by SHA until GitHub collects them, and
they survive in any fork or clone taken before the rewrite.

That forces a decision. **A published hostname cannot be unpublished. It can only
be retired.** The redaction stops the value spreading. It does not undo the
disclosure. Only destroying the resource makes the disclosed value meaningless.

Every name in `main.bicep` derives from `uniqueString(subscription().id,
environmentName)`. That is deliberate, because it keeps a redeploy idempotent. It
also means deleting the account and redeploying hands it the same name back.
Retiring a name required changing how that one name is derived.

The alternatives were worse. Deploying under a new `environmentName` renames
every resource in the environment. That abandons the storage account, the search
index and the OpenAI deployments to retire one hostname. A global salt does the
same thing less obviously. Hand-creating a replacement outside Bicep leaves the
template describing an account that no longer exists, and the next `deploy.sh`
run recreates the old name.

## Decision

**The Document Intelligence account name carries its own rotation salt.**

```bicep
param documentIntelligenceNameSalt string = 'r2'
var documentIntelligenceSuffix = uniqueString(
  subscription().id, environmentName, documentIntelligenceNameSalt
)
```

Bumping the salt produces a new deterministic name. A redeploy at the same salt
is still a no-op, so idempotency holds. `r1` denotes the original two-argument
form. The current deployment is `r2`.

No other resource is salted. This one is salted because its endpoint was
published. The parameter exists so the next disclosure costs one line instead of
an environment rebuild.

The old account was deleted, not merely renamed in the template. Bicep deploys
incrementally, so it would otherwise have left the account running under the
published hostname.

## Consequences

**The disclosed hostname no longer resolves.** The redaction alone could not
deliver that.

**The layout cache survived.** It keys on `(source_sha256, model_id,
api_version)` (ADR 0005) and not on the endpoint. All 25 cached layouts stayed
valid across the swap. `just corpus-parse` reports "would analyse 0, 25 already
cached", so re-analysis cost nothing. A cache keyed by endpoint would have turned
an endpoint rotation into a re-analysis bill. That is an argument for the key
that was not obvious when it was chosen.

**Rotation is now cheap.** One parameter, one deploy, one delete. There is no
longer a reason to tolerate a disclosure.

**A rotation still needs coordination.** Anything holding the old endpoint breaks
at the moment of deletion rather than degrading. That includes a local `.env`, a
Container App revision, and a colleague's shell. `deploy.sh` prints the new
export lines, which is how to pick it up.

**The old commits are still on GitHub.** The rewrite and force-push stop the
value spreading. They do not retract it. Asking GitHub Support to collect the
unreachable objects is the remaining step, and it is a request rather than a
guarantee.

**The real lesson is upstream.** The value entered the repository as an example
in a planning document. Nothing treats prose as sensitive and no reviewer looks
for values there. Write example endpoints as placeholders from the start, such as
`di-<workload>-<env>-<suffix>`. A real one costs this whole procedure.
