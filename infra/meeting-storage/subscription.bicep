targetScope = 'subscription'

param resourceGroupName string
param location string
param environmentName string

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module meetingStorage './main.bicep' = {
  name: 'meeting-storage-validation'
  scope: resourceGroup
  params: {
    environmentName: environmentName
    location: location
  }
}

output storageAccountUrl string = meetingStorage.outputs.storageAccountUrl
