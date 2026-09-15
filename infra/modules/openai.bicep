@description('Globally unique Azure OpenAI account name. Also used as the custom subdomain.')
param name string

@description('Azure region. Must offer both models on the Standard SKU.')
param location string

param tags object = {}

param chatDeploymentName string = 'chat'
param chatModelName string = 'gpt-4.1-mini'
param chatModelVersion string = '2025-04-14'
param chatCapacity int = 30

@description('gpt-4.1-mini has GlobalStandard quota in Sweden Central and no Standard quota at all.')
param chatDeploymentSku string = 'GlobalStandard'

param embeddingDeploymentName string = 'embeddings'
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingCapacity int = 50

@description('text-embedding-3-large is the mirror image: Standard quota is 350, GlobalStandard is zero.')
param embeddingDeploymentSku string = 'Standard'

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    // Token auth only works against a custom subdomain, not the regional endpoint.
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = {
  parent: account
  name: chatDeploymentName
  sku: {
    name: chatDeploymentSku
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource embeddings 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = {
  parent: account
  name: embeddingDeploymentName
  sku: {
    name: embeddingDeploymentSku
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  // Serialised deliberately: the account rejects concurrent deployment writes
  // with a 409, and Bicep would otherwise submit both at once.
  dependsOn: [
    chat
  ]
}

output id string = account.id
output name string = account.name
output endpoint string = account.properties.endpoint
output chatDeploymentName string = chat.name
output embeddingDeploymentName string = embeddings.name
