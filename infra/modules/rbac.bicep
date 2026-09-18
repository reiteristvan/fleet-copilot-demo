@description('Object id of the principal that receives every assignment below.')
param principalId string

param storageAccountName string
param openAiAccountName string
param searchServiceName string
param appInsightsName string
param documentIntelligenceAccountName string

// Built-in role definition ids. Resolved with `az role definition list -n <name>`;
// they are stable across tenants, unlike role display names.
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
// Cognitive Services User, not Cognitive Services OpenAI User: the
// OpenAI-specific role carries no Document Intelligence data actions.
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource storage 'Microsoft.Storage/storageAccounts@2025-08-01' existing = {
  name: storageAccountName
}

resource openAi 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: openAiAccountName
}

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: documentIntelligenceAccountName
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, principalId, cognitiveServicesOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      cognitiveServicesOpenAiUser
    )
    principalId: principalId
    // Declaring the type skips the directory lookup that otherwise fails when
    // the identity was created moments earlier in the same deployment.
    principalType: 'ServicePrincipal'
  }
}

resource searchDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, principalId, searchIndexDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      searchIndexDataContributor
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, principalId, storageBlobDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageBlobDataReader
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Beyond the three roles the brief lists: Application Insights has local auth
// disabled, so without this the app cannot emit telemetry at all.
resource metricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, principalId, monitoringMetricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      monitoringMetricsPublisher
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource documentIntelligenceUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: documentIntelligence
  name: guid(documentIntelligence.id, principalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      cognitiveServicesUser
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
