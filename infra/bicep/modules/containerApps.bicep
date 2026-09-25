param name string
param location string
param tags object
param managedIdentityId string
param managedIdentityClientId string
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
param containerImage string
param postgresFqdn string
param postgresDatabase string
param postgresUser string
@secure()
param postgresPassword string
param azureOpenAiEndpoint string
param azureOpenAiDeployment string
param azureOpenAiEmbeddingDeployment string
param azureSearchEndpoint string
param azureSearchIndex string
param storageAccountName string
param uploadsShareName string

var databaseUrl = 'postgresql+psycopg://${postgresUser}:${postgresPassword}@${postgresFqdn}:5432/${postgresDatabase}?sslmode=require'

resource env 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${name}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

// Container Apps mounts Azure Files over SMB with the account key (managed identity is
// not supported for SMB volume mounts). The key stays inside the environment resource.
resource uploadsStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: env
  name: 'uploads'
  properties: {
    azureFile: {
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: uploadsShareName
      accessMode: 'ReadWrite'
    }
  }
}

resource app 'Microsoft.App/containerApps@2023-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      activeRevisionsMode: 'Single'
      secrets: [
        { name: 'database-url', value: databaseUrl }
      ]
    }
    template: {
      containers: [
        {
          name: 'cmaa-api'
          image: containerImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_PROFILE', value: 'lz' }
            { name: 'USE_MANAGED_IDENTITY', value: 'true' }
            { name: 'MOCK_LLM', value: 'false' }
            { name: 'LOCAL_EMBEDDINGS', value: 'false' }
            { name: 'RAG_ENABLED', value: 'true' }
            { name: 'GUARDRAILS_ENABLED', value: 'true' }
            { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
            { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAiEndpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: azureOpenAiEmbeddingDeployment }
            { name: 'AZURE_SEARCH_ENDPOINT', value: azureSearchEndpoint }
            { name: 'AZURE_SEARCH_INDEX', value: azureSearchIndex }
            { name: 'STORAGE_DIR', value: '/data/uploads' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            // A pipeline lock every replica can see (the in-memory lock is per process).
            { name: 'PIPELINE_LOCK_BACKEND', value: 'db' }
          ]
          volumeMounts: [
            { volumeName: 'uploads', mountPath: '/data/uploads' }
          ]
        }
      ]
      volumes: [
        { name: 'uploads', storageType: 'AzureFile', storageName: uploadsStorage.name }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output environmentId string = env.id
output id string = app.id
