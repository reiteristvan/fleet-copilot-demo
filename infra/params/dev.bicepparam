using '../main.bicep'

param environmentName = 'dev'
param location = 'swedencentral'
param chatModelCapacity = 30
param embeddingModelCapacity = 50
param searchSku = 'basic'

// Resolved at deploy time by deploy.sh, never committed. An object id is not a
// credential, but it names a person, and a parameter file in a public repository
// is the wrong place to name one -- it also pins the environment to a single
// developer, so nobody else could deploy without editing this file.
//
// For a team, export the object id of an Entra security group instead and set
// the type to 'Group': adding a developer then becomes a membership change
// rather than a redeploy.
param developerPrincipalId = readEnvironmentVariable('AZURE_DEVELOPER_PRINCIPAL_ID', '')
param developerPrincipalType = 'User'
