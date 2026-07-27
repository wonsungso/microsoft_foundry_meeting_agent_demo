targetScope = 'resourceGroup'

@minLength(2)
param environmentName string

param location string = resourceGroup().location

var resourceToken = uniqueString(subscription().id, resourceGroup().id, environmentName)
var workspaceName = take('log-${environmentName}-${resourceToken}', 63)
var applicationInsightsName = take('appi-${environmentName}-${resourceToken}', 260)

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: {
    Environment: environmentName
    Workload: 'ai-glasses-meeting-agent'
  }
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: {
    Environment: environmentName
    Workload: 'ai-glasses-meeting-agent'
  }
  properties: {
    Application_Type: 'web'
    IngestionMode: 'LogAnalytics'
    WorkspaceResourceId: workspace.id
    DisableIpMasking: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output applicationInsightsId string = applicationInsights.id
output applicationInsightsName string = applicationInsights.name
output logAnalyticsWorkspaceId string = workspace.id
output logAnalyticsWorkspaceName string = workspace.name
