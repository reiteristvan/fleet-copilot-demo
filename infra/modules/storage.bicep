@description('Globally unique storage account name.')
param name string

@description('Azure region.')
param location string

param tags object = {}

@description('Container that raw source documents are uploaded to.')
param containerName string = 'raw-docs'

resource storage 'Microsoft.Storage/storageAccounts@2025-08-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Turning shared-key access off is what makes "no keys anywhere"
    // enforceable rather than merely intended: connection strings and SAS
    // tokens derived from the account key stop working entirely.
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-08-01' = {
  parent: storage
  name: 'default'
}

resource rawDocs 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-08-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output id string = storage.id
output name string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output containerName string = rawDocs.name
