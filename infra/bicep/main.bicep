targetScope = 'resourceGroup'

@description('Azure region for spoke resources')
param location string = resourceGroup().location

@description('Name prefix for spoke resources')
param namePrefix string = 'cmaa'

@description('Container image for the API (ACR or public placeholder)')
param containerImage string = 'mcr.microsoft.com/oss/nginx/nginx:1.25'

@secure()
@description('Postgres admin password')
param postgresAdminPassword string

@description('Shared Azure OpenAI / AI Foundry endpoint URL')
param azureOpenAiEndpoint string

@description('Shared Azure OpenAI resource ID for RBAC')
param azureOpenAiResourceId string = ''

@description('Chat deployment name')
param azureOpenAiDeployment string = 'gpt-4o'

@description('Embedding deployment name')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-3-small'

@description('Shared Azure AI Search endpoint')
param azureSearchEndpoint string

@description('Shared Azure AI Search resource ID for RBAC')
param azureSearchResourceId string = ''

@description('Search index name')
param azureSearchIndex string = 'cmaa-chunks'

var tags = {
  app: 'cloud-migration-assessment-accelerator'
  landingZone: 'application-spoke'
}

module law 'modules/logAnalytics.bicep' = {
  name: 'law'
  params: {
    name: 'log-${namePrefix}-${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: 'id-${namePrefix}'
    location: location
    tags: tags
  }
}

module kv 'modules/keyVault.bicep' = {
  name: 'kv'
  params: {
    name: 'kv-${namePrefix}${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
    tenantId: tenant().tenantId
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    name: 'st${namePrefix}${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    name: 'pg-${namePrefix}-${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
    administratorLoginPassword: postgresAdminPassword
  }
}

module roles 'modules/roleAssignments.bicep' = if (!empty(azureOpenAiResourceId) || !empty(azureSearchResourceId)) {
  name: 'roles'
  params: {
    principalId: identity.outputs.principalId
    azureOpenAiResourceId: azureOpenAiResourceId
    azureSearchResourceId: azureSearchResourceId
  }
}

module apps 'modules/containerApps.bicep' = {
  name: 'apps'
  params: {
    name: 'ca-${namePrefix}'
    location: location
    tags: tags
    managedIdentityId: identity.outputs.id
    managedIdentityClientId: identity.outputs.clientId
    logAnalyticsCustomerId: law.outputs.customerId
    logAnalyticsSharedKey: listKeys(law.outputs.id, '2022-10-01').primarySharedKey
    containerImage: containerImage
    postgresFqdn: postgres.outputs.fqdn
    postgresDatabase: postgres.outputs.databaseName
    postgresUser: 'cmaaadmin'
    postgresPassword: postgresAdminPassword
    azureOpenAiEndpoint: azureOpenAiEndpoint
    azureOpenAiDeployment: azureOpenAiDeployment
    azureOpenAiEmbeddingDeployment: azureOpenAiEmbeddingDeployment
    azureSearchEndpoint: azureSearchEndpoint
    azureSearchIndex: azureSearchIndex
    storageAccountName: storage.outputs.name
    uploadsShareName: storage.outputs.uploadsShareName
  }
}

output apiFqdn string = apps.outputs.fqdn
output managedIdentityClientId string = identity.outputs.clientId
output keyVaultUri string = kv.outputs.uri
output storageAccountName string = storage.outputs.name
output postgresFqdn string = postgres.outputs.fqdn
output nextSteps string = 'Point APIM backend to https://${apps.outputs.fqdn}. Set APP_PROFILE=lz on the container (already set). Replace placeholder image with your ACR build of apps/api.'
