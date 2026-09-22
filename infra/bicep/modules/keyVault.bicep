param name string
param location string
param tags object
param tenantId string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForTemplateDeployment: true
    enableSoftDelete: true
  }
}

output id string = kv.id
output uri string = kv.properties.vaultUri
