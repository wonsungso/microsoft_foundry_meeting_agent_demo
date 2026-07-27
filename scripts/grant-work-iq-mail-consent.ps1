param(
    [string]$UserObjectId,
    [string]$AgentName = 'notify-agent',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$agentToolsAppId = 'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1'
$mailScope = 'McpServers.Mail.All'

function Get-AgentToolsServicePrincipal {
    $servicePrincipalQuery = @"
{
    id: id,
    appId: appId,
    oauth2PermissionScopes: oauth2PermissionScopes[].{id: id, value: value}
}
"@ -replace "`r?`n", ''
    $output = az ad sp show `
        --id $agentToolsAppId `
        --query $servicePrincipalQuery `
        --output json 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $output) {
        return $output | ConvertFrom-Json
    }
    if ($output -match 'TokenCreatedWithOutdatedPolicies|Continuous access evaluation') {
        $account = az account show --output json | ConvertFrom-Json
        throw @"
Microsoft Graph requires fresh interactive authentication because Conditional Access policies changed.
Run the following commands, then run 'azd up' again:
  az logout --username $($account.user.name)
  az login --tenant $($account.tenantId) --scope https://graph.microsoft.com/.default
  az account set --subscription $($account.id)
"@
    }
    return $null
}

function Invoke-GraphWrite(
    [string]$Method,
    [string]$Url,
    [hashtable]$Payload
) {
    $bodyFile = [System.IO.Path]::GetTempFileName()
    try {
        $body = $Payload | ConvertTo-Json -Depth 5 -Compress
        [System.IO.File]::WriteAllText(
            $bodyFile,
            $body,
            [System.Text.UTF8Encoding]::new($false)
        )
        az rest `
            --method $Method `
            --url $Url `
            --headers 'Content-Type=application/json' `
            --body "@$bodyFile" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Microsoft Graph $Method request failed."
        }
    }
    finally {
        Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
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

$agentToolsServicePrincipal = Get-AgentToolsServicePrincipal
if ($PreflightOnly) {
    Write-Host 'Microsoft Graph authentication is ready for Work IQ Mail consent.'
    return
}

if (-not $UserObjectId) {
    $UserObjectId = Get-SignedInPrincipalId
}

$agent = azd ai agent show $AgentName --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not resolve the deployed $AgentName."
}

$blueprintAppId = $agent.blueprint.client_id
$blueprintPrincipalId = $agent.blueprint.principal_id
if (-not $blueprintAppId -or -not $blueprintPrincipalId) {
    throw "$AgentName does not expose an Agent Identity Blueprint."
}

$servicePrincipalQuery = @"
{
    id: id,
    appId: appId,
    oauth2PermissionScopes: oauth2PermissionScopes[].{id: id, value: value}
}
"@ -replace "`r?`n", ''
if (-not $agentToolsServicePrincipal) {
        $agentToolsServicePrincipal = az ad sp create `
                --id $agentToolsAppId `
                --query $servicePrincipalQuery `
                --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not provision the Microsoft Agent Tools service principal.'
    }
}

$mailScopeId = $agentToolsServicePrincipal.oauth2PermissionScopes |
    Where-Object { $_.value -eq $mailScope } |
    Select-Object -ExpandProperty id -First 1
if (-not $mailScopeId) {
    throw "The Agent Tools service principal does not expose $mailScope."
}

$blueprintApp = az ad app show --id $blueprintAppId | ConvertFrom-Json
$resourceAccess = $blueprintApp.requiredResourceAccess |
    Where-Object { $_.resourceAppId -eq $agentToolsAppId }
$permissionDeclared = $resourceAccess.resourceAccess |
    Where-Object { $_.id -eq $mailScopeId -and $_.type -eq 'Scope' }
if (-not $permissionDeclared) {
    az ad app permission add `
        --id $blueprintAppId `
        --api $agentToolsAppId `
        --api-permissions "$mailScopeId=Scope" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not add $mailScope to the Agent Identity Blueprint."
    }
}

$resourcePrincipalId = $agentToolsServicePrincipal.id
$filter = [Uri]::EscapeDataString(
    "clientId eq '$blueprintPrincipalId' and resourceId eq '$resourcePrincipalId'"
)
$grants = az rest --method get `
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=$filter" |
    ConvertFrom-Json
$grant = $grants.value |
    Where-Object { $_.consentType -eq 'Principal' -and $_.principalId -eq $UserObjectId } |
    Select-Object -First 1

if (-not $grant) {
    Invoke-GraphWrite -Method post `
        -Url 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' `
        -Payload @{
        clientId = $blueprintPrincipalId
        consentType = 'Principal'
        principalId = $UserObjectId
        resourceId = $resourcePrincipalId
        scope = $mailScope
    }
} elseif (($grant.scope -split ' ') -notcontains $mailScope) {
    $scope = (($grant.scope -split ' ') + $mailScope | Sort-Object -Unique) -join ' '
    Invoke-GraphWrite -Method patch `
        -Url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/$($grant.id)" `
        -Payload @{ scope = $scope }
}

Write-Host "Granted $mailScope to user $UserObjectId for $AgentName."
