@description('Globally unique Azure AI Search service name.')
param name string

@description('Azure region.')
param location string

param tags object = {}

@allowed([
  'basic'
  'standard'
])
param sku string = 'basic'

@description('Semantic ranker plan. "free" allows 1000 queries/month at no cost.')
@allowed([
  'free'
  'standard'
])
param semanticSearch string = 'free'

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: semanticSearch
    // authOptions must be omitted entirely when local auth is off; setting
    // both is rejected at deployment time.
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
