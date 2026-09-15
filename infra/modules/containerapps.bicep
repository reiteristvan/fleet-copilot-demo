@description('Container Apps managed environment name.')
param name string

@description('Azure region.')
param location string

@description('Log Analytics workspace that environment logs are routed to.')
param logAnalyticsWorkspaceId string

param tags object = {}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      // The 'log-analytics' destination requires the workspace shared key in
      // the template. 'azure-monitor' routes through the diagnostic setting
      // below instead, so no key is ever read or passed.
      destination: 'azure-monitor'
    }
    zoneRedundant: false
  }
}

resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'send-to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

output id string = environment.id
output name string = environment.name
output defaultDomain string = environment.properties.defaultDomain
