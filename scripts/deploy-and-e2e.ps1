$ErrorActionPreference = 'Stop'

if ($env:AZD_SEQUENTIAL_AGENT_DEPLOY -eq '1') {
    return
}
$env:AZD_SEQUENTIAL_AGENT_DEPLOY = '1'

function Import-AzdEnvironment {
    $azdValues = azd env get-values --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not load the active azd environment.'
    }
    foreach ($property in $azdValues.PSObject.Properties) {
        Set-Item "Env:$($property.Name)" ([string]$property.Value)
    }
}

Import-AzdEnvironment

& "$PSScriptRoot/ensure-foundry-rbac.ps1"
& "$PSScriptRoot/provision-storage.ps1"
& "$PSScriptRoot/provision-observability.ps1"
& "$PSScriptRoot/ensure-work-iq-toolbox.ps1"
Import-AzdEnvironment
& "$PSScriptRoot/show-spa.ps1"

Write-Host ''
Write-Host 'Deploying Microsoft Foundry Hosted Agents sequentially before integrated validation...'

$agentServices = @(
    'context-agent',
    'triage-agent',
    'meeting-agent',
    'summarizer-agent',
    'notify-agent'
)

foreach ($service in $agentServices) {
    $deployed = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        Write-Host "Deploying $service (attempt $attempt/5)..."
        azd deploy $service --timeout 180 --no-prompt
        if ($LASTEXITCODE -eq 0) {
            $deployed = $true
            break
        }

        if ($attempt -lt 5) {
            $delaySeconds = 5 * $attempt
            Write-Warning "$service deployment failed; retrying in $delaySeconds seconds."
            Start-Sleep -Seconds $delaySeconds
        }
    }

    if (-not $deployed) {
        throw "Microsoft Foundry Hosted Agent deployment failed for '$service'."
    }
}

$agentToken = az account get-access-token `
    --scope https://ai.azure.com/.default `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $agentToken) {
    throw 'Could not obtain a Foundry access token for legacy Agent cleanup.'
}
$legacyAgentUrl = "$($env:FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/'))/agents/meeting-notes-agent?api-version=v1&force=true"
try {
    Invoke-RestMethod `
        -Method Delete `
        -Uri $legacyAgentUrl `
        -Headers @{ Authorization = "Bearer $agentToken" } | Out-Null
    Write-Host 'Deleted legacy Hosted Agent meeting-notes-agent.'
}
catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) {
        throw
    }
}

& "$PSScriptRoot/postdeploy-e2e.ps1"