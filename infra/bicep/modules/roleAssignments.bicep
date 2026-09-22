param principalId string

@description('Resource ID of shared Azure OpenAI / AI Foundry cognitive account')
param azureOpenAiResourceId string

@description('Resource ID of shared Azure AI Search service')
param azureSearchResourceId string

// Cognitive Services OpenAI User
var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
// Search Index Data Contributor
var searchDataContributor = '8ebe5a00-799e-43f5-93ac-875751fbf947'

resource openai 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = if (!empty(azureOpenAiResourceId)) {
  name: last(split(azureOpenAiResourceId, '/'))
  scope: resourceGroup(split(azureOpenAiResourceId, '/')[2], split(azureOpenAiResourceId, '/')[4])
}

resource search 'Microsoft.Search/searchServices@2023-11-01' existing = if (!empty(azureSearchResourceId)) {
  name: last(split(azureSearchResourceId, '/'))
  scope: resourceGroup(split(azureSearchResourceId, '/')[2], split(azureSearchResourceId, '/')[4])
}

resource openaiRa 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(azureOpenAiResourceId)) {
  name: guid(azureOpenAiResourceId, principalId, openAiUserRole)
  scope: openai
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchRa 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(azureSearchResourceId)) {
  name: guid(azureSearchResourceId, principalId, searchDataContributor)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchDataContributor)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
