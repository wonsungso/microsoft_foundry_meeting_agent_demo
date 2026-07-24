$ErrorActionPreference = 'Stop'

function Get-SignedInPrincipalId {
    $accessToken = az account get-access-token `
        --resource https://management.azure.com/ `
        --query accessToken `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $accessToken) {
        throw 'Could not obtain an Azure Resource Manager access token.'
    }

    $payload = $accessToken.Split('.')[1].Replace('-', '+').Replace('_', '/')
    $payload += '=' * ((4 - $payload.Length % 4) % 4)
    $claims = [System.Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($payload)
    ) | ConvertFrom-Json
    if (-not $claims.oid) {
        throw 'The Azure Resource Manager access token does not contain an oid claim.'
    }

    return [string]$claims.oid
}

$requiredVariables = @('AZURE_RESOURCE_GROUP', 'AZURE_LOCATION', 'AZURE_ENV_NAME')
foreach ($variableName in $requiredVariables) {
    if (-not (Get-Item "Env:$variableName" -ErrorAction SilentlyContinue).Value) {
        throw "Required environment variable $variableName is not set."
    }
}

$templateFile = Join-Path $PSScriptRoot '..\infra\meeting-storage\main.bicep'
$storageUrl = az deployment group create `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --name "meeting-storage-$($env:AZURE_ENV_NAME)" `
    --template-file $templateFile `
    --parameters environmentName=$env:AZURE_ENV_NAME location=$env:AZURE_LOCATION `
    --query properties.outputs.storageAccountUrl.value `
    --output tsv

if (-not $storageUrl) {
    throw 'Storage deployment did not return a Blob service URL.'
}

$storageAccountName = ([Uri]$storageUrl).Host.Split('.')[0]
$storageAccountId = az storage account show `
    --name $storageAccountName `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query id `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $storageAccountId) {
    throw 'Could not resolve the meeting-minutes Storage account ID.'
}

$userPrincipalId = Get-SignedInPrincipalId
$blobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
$existingReader = az role assignment list `
    --assignee-object-id $userPrincipalId `
    --scope $storageAccountId `
    --include-inherited `
    --query "[?roleDefinitionId=='/subscriptions/$($env:AZURE_SUBSCRIPTION_ID)/providers/Microsoft.Authorization/roleDefinitions/$blobDataReader'] | [0].id" `
    --output tsv
if (-not $existingReader) {
    az role assignment create `
        --assignee-object-id $userPrincipalId `
        --assignee-principal-type User `
        --role $blobDataReader `
        --scope $storageAccountId `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not grant Storage Blob Data Reader to the signed-in user.'
    }
}

azd env set AZURE_STORAGE_ACCOUNT_URL $storageUrl | Out-Null
Write-Host "Meeting minutes storage is ready: $storageUrl"
Write-Host "Granted Storage Blob Data Reader to signed-in user $userPrincipalId."