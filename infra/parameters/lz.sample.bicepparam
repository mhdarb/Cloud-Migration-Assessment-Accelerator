using '../bicep/main.bicep'

param location = 'eastus'
param namePrefix = 'cmaa'
param containerImage = 'mcr.microsoft.com/oss/nginx/nginx:1.25' // replace with ACR image of apps/api
param postgresAdminPassword = 'CHANGE_ME_Complex_P@ssw0rd!'
param azureOpenAiEndpoint = 'https://YOUR-AOAI.openai.azure.com/'
param azureOpenAiResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-ai-hub/providers/Microsoft.CognitiveServices/accounts/YOUR-AOAI'
param azureOpenAiDeployment = 'gpt-4o'
param azureOpenAiEmbeddingDeployment = 'text-embedding-3-small'
param azureSearchEndpoint = 'https://YOUR-SEARCH.search.windows.net'
param azureSearchResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-ai-hub/providers/Microsoft.Search/searchServices/YOUR-SEARCH'
param azureSearchIndex = 'cmaa-chunks'
