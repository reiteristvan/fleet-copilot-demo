using '../main.bicep'

param environmentName = 'dev'
param location = 'swedencentral'
param chatModelCapacity = 30
param embeddingModelCapacity = 50
param searchSku = 'basic'

// The object id of the developer working on this environment, from
// `az ad signed-in-user show --query id -o tsv`. Not a secret and not a service
// account -- it names an Entra principal that already exists. For a team, point
// this at a security group instead and set the type to 'Group', so adding a
// developer is a membership change rather than a redeploy.
param developerPrincipalId = '00000000-0000-0000-0000-000000000000'
param developerPrincipalType = 'User'
