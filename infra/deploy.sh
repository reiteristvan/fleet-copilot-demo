#!/usr/bin/env bash
#
# Deploy the fleet-copilot infrastructure.
#
#   ./infra/deploy.sh dev
#   ./infra/deploy.sh ci --what-if
#
# Safe to re-run: every resource in main.bicep is declarative and the derived
# names are stable, so a second run is a no-op.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly WORKLOAD="fleet-copilot"

# ARM auto-registers providers, but serially and only once it reaches a resource
# that needs one. Registering up front in parallel keeps a cold subscription
# inside the ten-minute target.
readonly PROVIDERS=(
  Microsoft.AlertsManagement
  Microsoft.App
  Microsoft.Authorization
  Microsoft.CognitiveServices
  Microsoft.Insights
  Microsoft.KeyVault
  Microsoft.ManagedIdentity
  Microsoft.OperationalInsights
  Microsoft.Search
  Microsoft.Storage
)

usage() {
  cat >&2 <<'USAGE'
usage: deploy.sh <dev|ci> [--what-if] [--validate]

  --what-if   show the changes the deployment would make, then exit
  --validate  run template validation only, then exit
USAGE
  exit 2
}

die() {
  printf 'deploy.sh: %s\n' "$1" >&2
  exit 1
}

main() {
  local environment="${1:-}"
  shift || true

  case "$environment" in
    dev | ci) ;;
    *) usage ;;
  esac

  local mode="create"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --what-if) mode="what-if" ;;
      --validate) mode="validate" ;;
      *) usage ;;
    esac
    shift
  done

  local param_file="${SCRIPT_DIR}/params/${environment}.bicepparam"
  local template="${SCRIPT_DIR}/main.bicep"
  [[ -f "$param_file" ]] || die "no parameter file at ${param_file}"

  command -v az >/dev/null || die "the Azure CLI is not on PATH"
  az account show >/dev/null 2>&1 || die "not signed in; run 'az login'"

  local subscription
  subscription="$(az account show --query name -o tsv)"

  local location
  location="$(deployment_location "$param_file")"

  printf 'subscription : %s\n' "$subscription"
  printf 'environment  : %s\n' "$environment"
  printf 'location     : %s\n' "$location"
  printf 'mode         : %s\n\n' "$mode"

  resolve_developer_principal
  register_providers

  local deployment_name="${WORKLOAD}-${environment}-$(date -u +%Y%m%d%H%M%S)"

  case "$mode" in
    what-if)
      az deployment sub what-if \
        --name "$deployment_name" \
        --location "$location" \
        --template-file "$template" \
        --parameters "$param_file"
      ;;
    validate)
      az deployment sub validate \
        --name "$deployment_name" \
        --location "$location" \
        --template-file "$template" \
        --parameters "$param_file" \
        --output none
      printf 'template is valid\n'
      ;;
    create)
      az deployment sub create \
        --name "$deployment_name" \
        --location "$location" \
        --template-file "$template" \
        --parameters "$param_file" \
        --output none
      print_app_env "$deployment_name"
      ;;
  esac
}

# The template creates the resource group itself, so --location on a
# subscription-scoped deployment only decides where the deployment record is
# stored. Reading it from the parameter file stops the two from drifting.
deployment_location() {
  local param_file="$1"
  az bicep build-params --file "$param_file" --stdout | python -c '
import json, sys

build = json.load(sys.stdin)
parameters = json.loads(build["parametersJson"])["parameters"]
print(parameters["location"]["value"])
'
}

# Data-plane roles go to whoever is deploying. Resolved here rather than
# committed to a parameter file: an object id is not a credential, but it names
# a person, and it would also pin the environment to one developer.
# Empty is valid -- dev.bicepparam defaults to it and the assignments are
# conditional -- so CI and a service principal deploy without one.
resolve_developer_principal() {
  AZURE_DEVELOPER_PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null | tr -d '')"
  export AZURE_DEVELOPER_PRINCIPAL_ID
  if [[ -n "$AZURE_DEVELOPER_PRINCIPAL_ID" ]]; then
    # The id itself is not printed: it names a person, and this output is
    # pasted into issues and captured by CI.
    printf 'developer    : resolved from the signed-in session

'
  else
    printf 'developer    : none; data-plane roles will not be granted

'
  fi
}

register_providers() {
  local ns
  for ns in "${PROVIDERS[@]}"; do
    az provider register --namespace "$ns" --only-show-errors >/dev/null 2>&1 &
  done
  wait
}

# The app reads these from the environment. AZURE_CLIENT_ID is what makes
# DefaultAzureCredential pick the user-assigned identity in Container Apps;
# leave it unset locally and the same class falls through to the az login.
print_app_env() {
  local deployment_name="$1"

  # Environment variable per deployment output, in the query's order.
  local -a env_names=(
    AZURE_CLIENT_ID
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_CHAT_DEPLOYMENT
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    AZURE_SEARCH_ENDPOINT
    AZURE_STORAGE_BLOB_ENDPOINT
    AZURE_STORAGE_CONTAINER
    AZURE_LAYOUT_CACHE_CONTAINER
    AZURE_KEY_VAULT_URI
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
    APPLICATIONINSIGHTS_CONNECTION_STRING
  )

  # `az -o tsv` prints one array element per line, and adds CR on Windows.
  local -a values
  mapfile -t values < <(
    az deployment sub show --name "$deployment_name" --output tsv --query "[
      properties.outputs.managedIdentityClientId.value,
      properties.outputs.openAiEndpoint.value,
      properties.outputs.openAiChatDeployment.value,
      properties.outputs.openAiEmbeddingDeployment.value,
      properties.outputs.searchEndpoint.value,
      properties.outputs.storageBlobEndpoint.value,
      properties.outputs.storageContainerName.value,
      properties.outputs.storageLayoutCacheContainer.value,
      properties.outputs.keyVaultUri.value,
      properties.outputs.documentIntelligenceEndpoint.value,
      properties.outputs.appInsightsConnectionString.value
    ]" | tr -d ''
  )

  if [[ ${#values[@]} -ne ${#env_names[@]} ]]; then
    die "expected ${#env_names[@]} deployment outputs, got ${#values[@]}"
  fi

  printf '
# deployment outputs -- source these for a local run
'
  local index
  for index in "${!env_names[@]}"; do
    printf 'export %s="%s"
' "${env_names[index]}" "${values[index]}"
  done
}

main "$@"
