param name string
param location string
param tags object

resource st 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: st
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blob
  name: 'uploads'
  properties: { publicAccess: 'None' }
}

// Uploaded evidence, extracted code snapshots and client questionnaires. The API reads
// and writes these as ordinary files under STORAGE_DIR, so they live on an Azure Files
// share mounted into every replica — the container's own disk is lost on restart and
// isn't shared between replicas.
@description('Quota for the uploads file share, in GiB.')
param uploadsShareQuotaGiB int = 100

resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: st
  name: 'default'
}

resource uploadsShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: files
  name: 'uploads'
  properties: {
    shareQuota: uploadsShareQuotaGiB
    enabledProtocols: 'SMB'
  }
}

output id string = st.id
output name string = st.name
output blobEndpoint string = st.properties.primaryEndpoints.blob
output uploadsShareName string = uploadsShare.name
