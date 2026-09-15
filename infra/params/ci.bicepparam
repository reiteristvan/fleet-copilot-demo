using '../main.bicep'

param environmentName = 'ci'
param location = 'swedencentral'
// CI only has to prove the wiring works. Keeping its capacity small is what
// lets dev and ci share one subscription's model quota.
param chatModelCapacity = 10
param embeddingModelCapacity = 10
param searchSku = 'basic'
