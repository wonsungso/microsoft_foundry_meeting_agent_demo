$ErrorActionPreference = 'Stop'

if (-not $env:AZURE_SUBSCRIPTION_ID -or -not $env:AZURE_AI_PROJECT_ID -or -not $env:AZURE_STORAGE_ACCOUNT_URL) {
    Write-Warning 'Subscription, Foundry project, or Storage settings are unavailable; skipping agent RBAC assignment.'
    exit 0
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
            principalType = 'ServicePrincipal'
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
            throw "Failed to assign role $RoleDefinitionId to Hosted Agent $PrincipalId."
        }
    }
    finally {
        Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
    }
}

$storageAccountName = ([Uri]$env:AZURE_STORAGE_ACCOUNT_URL).Host.Split('.')[0]
$storageAccountId = az storage account show `
    --name $storageAccountName `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --query id `
    --output tsv

$accountScope = $env:AZURE_AI_PROJECT_ID -replace '/projects/[^/]+$', ''
$foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
$blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
$agentNames = @(
    'triage-agent',
    'context-agent',
    'meeting-agent',
    'summarizer-agent',
    'notify-agent'
)

foreach ($agentName in $agentNames) {
    $agent = azd ai agent show $agentName --output json | ConvertFrom-Json
    $principalId = $agent.instance_identity.principal_id
    if (-not $principalId) {
        throw "Hosted Agent '$agentName' does not expose an instance identity."
    }

    Set-RoleAssignment $accountScope $principalId $foundryUser
    Set-RoleAssignment $env:AZURE_AI_PROJECT_ID $principalId $foundryUser
    if ($agentName -eq 'meeting-agent') {
        Set-RoleAssignment $storageAccountId $principalId $blobDataContributor
    }
    Write-Host "Granted runtime access to $agentName identity $principalId."
}

Write-Host 'Granted Foundry runtime access to all Hosted Agents and Blob write access to Meeting Agent.'