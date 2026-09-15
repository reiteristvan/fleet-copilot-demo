@description('Globally unique Key Vault name (24 characters maximum).')
param name string

@description('Azure region.')
param location string

param tags object = {}

@description('Soft-delete retention. 7 is the minimum, chosen so a torn-down environment can be purged and redeployed the same week.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 7

resource vault 'Microsoft.KeyVault/vaults@2025-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    // RBAC rather than access policies, so vault permissions are granted the
    // same way as every other data plane in this deployment.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    publicNetworkAccess: 'Enabled'
  }
}

output id string = vault.id
output name string = vault.name
output uri string = vault.properties.vaultUri
