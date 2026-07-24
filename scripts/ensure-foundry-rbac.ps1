$ErrorActionPreference = 'Stop'

if (-not $env:AZURE_AI_PROJECT_ID -or -not $env:AZURE_SUBSCRIPTION_ID) {
    throw 'AZURE_AI_PROJECT_ID and AZURE_SUBSCRIPTION_ID are required.'
}

function New-DeterministicGuid([string]$Value) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))
        $guidBytes = [byte[]]::new(16)
        [Array]::Copy($hash, $guidBytes, 16)
        return [Guid]::new($guidBytes).ToString()
    }
    finally {
        $sha256.Dispose()
    }
}

function Set-RoleAssignment(
    [string]$Scope,
    [string]$PrincipalId,
    [string]$PrincipalType,
    [string]$RoleDefinitionId
) {
    $existingAssignments = az role assignment list `
        --assignee-object-id $PrincipalId `
        --scope $Scope `
        --include-inherited `
        --output json | ConvertFrom-Json
    $existingAssignment = $existingAssignments | Where-Object {
        $_.scope -eq $Scope -and
        $_.roleDefinitionId -like "*/$RoleDefinitionId"
    } | Select-Object -First 1
    if ($existingAssignment) {
        return
    }

    $assignmentId = New-DeterministicGuid "$Scope|$PrincipalId|$RoleDefinitionId"
    $body = @{
        properties = @{
            roleDefinitionId = "/subscriptions/$($env:AZURE_SUBSCRIPTION_ID)/providers/Microsoft.Authorization/roleDefinitions/$RoleDefinitionId"
            principalId = $PrincipalId
            principalType = $PrincipalType
        }
    } | ConvertTo-Json -Depth 4 -Compress

    $bodyFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($bodyFile, $body)
        $response = az rest `
            --method put `
            --url "https://management.azure.com$Scope/providers/Microsoft.Authorization/roleAssignments/$assignmentId`?api-version=2022-04-01" `
            --headers Content-Type=application/json `
            --body "@$bodyFile" `
            --output none 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -and $response -notmatch 'RoleAssignmentExists') {
            throw "Failed to assign role $RoleDefinitionId to principal $PrincipalId."
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

$accountScope = $env:AZURE_AI_PROJECT_ID -replace '/projects/[^/]+$', ''
$project = az rest `
    --method get `
    --url "https://management.azure.com$($env:AZURE_AI_PROJECT_ID)?api-version=2025-06-01" |
    ConvertFrom-Json
$projectPrincipalId = $project.identity.principalId
$userPrincipalId = Get-SignedInPrincipalId

$foundryProjectManager = 'eadc314b-1a2d-4efa-be10-5d325db5065e'
$foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

Set-RoleAssignment $env:AZURE_AI_PROJECT_ID $userPrincipalId 'User' $foundryProjectManager
Set-RoleAssignment $env:AZURE_AI_PROJECT_ID $userPrincipalId 'User' $foundryUser
Set-RoleAssignment $accountScope $projectPrincipalId 'ServicePrincipal' $foundryUser

Write-Host 'Foundry user and project identity RBAC assignments are configured.'