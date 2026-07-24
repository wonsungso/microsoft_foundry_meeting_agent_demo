$ErrorActionPreference = 'Stop'

$requiredVariables = @(
    'AZURE_SUBSCRIPTION_ID',
    'AZURE_RESOURCE_GROUP',
    'AZURE_LOCATION',
    'AZURE_ENV_NAME',
    'AZURE_AI_PROJECT_ID'
)
foreach ($variableName in $requiredVariables) {
    if (-not (Get-Item "Env:$variableName" -ErrorAction SilentlyContinue).Value) {
        throw "Required environment variable $variableName is not set."
    }
}

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

function Set-RoleAssignment(
    [string]$Scope,
    [string]$PrincipalId,
    [string]$PrincipalType,
    [string]$RoleDefinitionId
) {
    $existing = az role assignment list `
        --assignee-object-id $PrincipalId `
        --scope $Scope `
        --include-inherited `
        --output json | ConvertFrom-Json
    $match = $existing | Where-Object {
        $_.scope -eq $Scope -and $_.roleDefinitionId -like "*/$RoleDefinitionId"
    } | Select-Object -First 1
    if ($match) {
        return
    }

    az role assignment create `
        --assignee-object-id $PrincipalId `
        --assignee-principal-type $PrincipalType `
        --role $RoleDefinitionId `
        --scope $Scope `
        --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Could not assign role $RoleDefinitionId to $PrincipalId."
    }
}

$templateFile = Join-Path $PSScriptRoot '..\infra\observability\main.bicep'
$outputs = az deployment group create `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --name "meeting-observability-$($env:AZURE_ENV_NAME)" `
    --template-file $templateFile `
    --parameters environmentName=$env:AZURE_ENV_NAME location=$env:AZURE_LOCATION `
    --query properties.outputs `
    --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $outputs.applicationInsightsId.value) {
    throw 'Application Insights deployment did not return its resource ID.'
}

$applicationInsightsId = [string]$outputs.applicationInsightsId.value
$workspaceId = [string]$outputs.logAnalyticsWorkspaceId.value
$component = az rest `
    --method get `
    --url "https://management.azure.com$applicationInsightsId`?api-version=2020-02-02" `
    --output json | ConvertFrom-Json
$connectionString = [string]$component.properties.ConnectionString
if ($LASTEXITCODE -ne 0 -or -not $connectionString) {
    throw 'Could not resolve the Application Insights connection string.'
}

azd ai connection create ApplicationInsights `
    --kind app-insights `
    --target $applicationInsightsId `
    --auth-type api-key `
    --key $connectionString `
    --force `
    --no-prompt | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not connect Application Insights to the Foundry project.'
}

$project = az rest `
    --method get `
    --url "https://management.azure.com$($env:AZURE_AI_PROJECT_ID)?api-version=2025-06-01" `
    --output json | ConvertFrom-Json
$projectPrincipalId = [string]$project.identity.principalId
$userPrincipalId = Get-SignedInPrincipalId
$monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
$logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'

Set-RoleAssignment $applicationInsightsId $userPrincipalId 'User' $monitoringReader
Set-RoleAssignment $workspaceId $userPrincipalId 'User' $logAnalyticsReader
if ($projectPrincipalId) {
    Set-RoleAssignment $applicationInsightsId $projectPrincipalId 'ServicePrincipal' $monitoringReader
    Set-RoleAssignment $workspaceId $projectPrincipalId 'ServicePrincipal' $logAnalyticsReader
}

azd env set `
    "APPLICATIONINSIGHTS_RESOURCE_ID=$applicationInsightsId" `
    "LOG_ANALYTICS_WORKSPACE_RESOURCE_ID=$workspaceId" `
    --no-prompt | Out-Null

Write-Host "Application Insights tracing is connected: $applicationInsightsId"
Write-Host "Granted trace read access to signed-in user $userPrincipalId."