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
