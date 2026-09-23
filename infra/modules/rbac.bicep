@description('Object id of the principal that receives every assignment below.')
param principalId string

param storageAccountName string
param openAiAccountName string
param searchServiceName string
param appInsightsName string
param documentIntelligenceAccountName string

@description('Object id of a human principal that also needs data-plane access. Empty disables it.')
param developerPrincipalId string = ''

@description('What developerPrincipalId is. A user object id assigned as ServicePrincipal never resolves.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param developerPrincipalType string = 'User'

// Built-in role definition ids. Resolved with `az role definition list -n <name>`;
// they are stable across tenants, unlike role display names.
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
// Creating an index and writing documents are different roles. Service
// Contributor manages index definitions and grants no document access;
// Index Data Contributor reads and writes documents and cannot create an
// index. A developer who hand-designs the index in code needs both, and the
// runtime identity deliberately gets only the second.
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
// Cognitive Services User, not Cognitive Services OpenAI User: the
// OpenAI-specific role carries no Document Intelligence data actions.
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

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

// Subscription Owner is a management-plane role and carries no data actions, so
// a developer who can create these resources still gets a 403 from the first
// request against one. These three assignments are what let the offline steps --
// capturing layout fixtures, writing the layout cache, embedding the corpus --
// run from a laptop. Empty in CI, which never calls Azure.
resource developerDocumentIntelligence 'Microsoft.Authorization/roleAssignments@2022-04-01' =
  if (!empty(developerPrincipalId)) {
    scope: documentIntelligence
    name: guid(documentIntelligence.id, developerPrincipalId, cognitiveServicesUser)
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        cognitiveServicesUser
      )
      principalId: developerPrincipalId
      principalType: developerPrincipalType
    }
  }

// Cognitive Services OpenAI User, not the Cognitive Services User granted on
// Document Intelligence above: the OpenAI data plane is gated by its own role,
// and holding the other one produces a 401 naming a data action rather than a
// role, which reads like a broken token instead of a missing assignment.
resource developerOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' =
  if (!empty(developerPrincipalId)) {
    scope: openAi
    name: guid(openAi.id, developerPrincipalId, cognitiveServicesOpenAiUser)
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        cognitiveServicesOpenAiUser
      )
      principalId: developerPrincipalId
      principalType: developerPrincipalType
    }
  }

resource developerSearchService 'Microsoft.Authorization/roleAssignments@2022-04-01' =
  if (!empty(developerPrincipalId)) {
    scope: search
    name: guid(search.id, developerPrincipalId, searchServiceContributor)
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        searchServiceContributor
      )
      principalId: developerPrincipalId
      principalType: developerPrincipalType
    }
  }

resource developerSearchData 'Microsoft.Authorization/roleAssignments@2022-04-01' =
  if (!empty(developerPrincipalId)) {
    scope: search
    name: guid(search.id, developerPrincipalId, searchIndexDataContributor)
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        searchIndexDataContributor
      )
      principalId: developerPrincipalId
      principalType: developerPrincipalType
    }
  }

// Contributor, not Reader: the managed identity only reads the cache, but the
// developer who populates it has to write.
resource developerStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' =
  if (!empty(developerPrincipalId)) {
    scope: storage
    name: guid(storage.id, developerPrincipalId, storageBlobDataContributor)
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        storageBlobDataContributor
      )
      principalId: developerPrincipalId
      principalType: developerPrincipalType
    }
  }
