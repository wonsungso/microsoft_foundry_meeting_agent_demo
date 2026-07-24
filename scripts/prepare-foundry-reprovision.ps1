$ErrorActionPreference = 'Stop'

$azdValues = azd env get-values --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw 'Could not load the active azd environment.'
}

$resourceGroup = [string]$azdValues.AZURE_RESOURCE_GROUP
if (-not $resourceGroup) {
    throw 'The active azd environment is missing AZURE_RESOURCE_GROUP.'
}
$subscriptionId = [string]$azdValues.AZURE_SUBSCRIPTION_ID
$tenantId = [string]$azdValues.AZURE_TENANT_ID
$location = [string]$azdValues.AZURE_LOCATION
$account = az account show --output json | ConvertFrom-Json
if (-not $subscriptionId) {
    $subscriptionId = [string]$account.id
}
if (-not $tenantId) {
    $tenantId = [string]$account.tenantId
}
if (-not $location) {
    $location = [string]$azdValues.AZURE_AI_DEPLOYMENTS_LOCATION
}
if (-not $subscriptionId -or -not $tenantId -or -not $location) {
    throw 'Could not preserve the subscription, tenant, and location for the next azd up.'
}

$resourceGroupBase = $resourceGroup -replace '-[0-9a-f]{6}$', ''
$resourceGroupSuffix = [Guid]::NewGuid().ToString('N').Substring(0, 6)
$nextResourceGroup = "$resourceGroupBase-$resourceGroupSuffix"

azd env set `
    "AZURE_RESOURCE_GROUP=$nextResourceGroup" `
    "AZURE_SUBSCRIPTION_ID=$subscriptionId" `
    "AZURE_TENANT_ID=$tenantId" `
    "AZURE_LOCATION=$location" `
    --no-prompt
if ($LASTEXITCODE -ne 0) {
    throw 'Could not rotate AZURE_RESOURCE_GROUP for Foundry reprovisioning.'
}

Write-Host "Prepared a clean Foundry reprovisioning target (resource group: $nextResourceGroup)."