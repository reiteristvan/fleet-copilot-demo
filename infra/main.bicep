targetScope = 'subscription'

@description('Environment slug. Becomes part of every resource name.')
@allowed([
  'dev'
  'ci'
])
param environmentName string

@description('Azure region. Sweden Central is the default because it is the only nearby region offering both models on the Standard SKU.')
param location string = 'swedencentral'

@description('Thousands of tokens per minute for the chat deployment.')
param chatModelCapacity int = 30

@description('Thousands of tokens per minute for the embedding deployment.')
param embeddingModelCapacity int = 50

@description('Azure AI Search tier.')
param searchSku string = 'basic'

var workload = 'fleet-copilot'

// Storage, Key Vault, OpenAI and Search names must be globally unique. Deriving
// the suffix from the subscription id keeps a redeploy idempotent while still
// avoiding collisions with anyone else's deployment of this template.
var suffix = uniqueString(subscription().id, environmentName)

var tags = {
  workload: workload
  environment: environmentName
  managedBy: 'bicep'
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-${workload}-${environmentName}'
  location: location
  tags: tags
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: {
    name: 'id-${workload}-${environmentName}'
    location: location
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    workspaceName: 'log-${workload}-${environmentName}'
    appInsightsName: 'appi-${workload}-${environmentName}'
    location: location
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  scope: rg
  name: 'storage'
  params: {
    name: 'stfleet${environmentName}${suffix}'
    location: location
    tags: tags
  }
}

module openAi 'modules/openai.bicep' = {
  scope: rg
  name: 'openai'
  params: {
    name: 'oai-${workload}-${environmentName}-${suffix}'
    location: location
    tags: tags
    chatCapacity: chatModelCapacity
    embeddingCapacity: embeddingModelCapacity
  }
}

module search 'modules/search.bicep' = {
  scope: rg
  name: 'search'
  params: {
    name: 'srch-${workload}-${environmentName}-${suffix}'
    location: location
    tags: tags
    sku: searchSku
  }
}

module keyVault 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    name: 'kv-fleet-${environmentName}-${take(suffix, 8)}'
    location: location
    tags: tags
  }
}

module containerApps 'modules/containerapps.bicep' = {
  scope: rg
  name: 'containerapps'
  params: {
    name: 'cae-${workload}-${environmentName}'
    location: location
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    tags: tags
  }
}

module rbac 'modules/rbac.bicep' = {
  scope: rg
  name: 'rbac'
  params: {
    principalId: identity.outputs.principalId
    storageAccountName: storage.outputs.name
    openAiAccountName: openAi.outputs.name
    searchServiceName: search.outputs.name
    appInsightsName: monitoring.outputs.appInsightsName
  }
}

output resourceGroupName string = rg.name
output managedIdentityClientId string = identity.outputs.clientId
output managedIdentityPrincipalId string = identity.outputs.principalId
output openAiEndpoint string = openAi.outputs.endpoint
output openAiChatDeployment string = openAi.outputs.chatDeploymentName
output openAiEmbeddingDeployment string = openAi.outputs.embeddingDeploymentName
output searchEndpoint string = search.outputs.endpoint
output storageBlobEndpoint string = storage.outputs.blobEndpoint
output storageContainerName string = storage.outputs.containerName
output keyVaultUri string = keyVault.outputs.uri
output containerAppsEnvironmentId string = containerApps.outputs.id
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
