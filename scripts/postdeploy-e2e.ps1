$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-DeploymentResult {
    param([Parameter(Mandatory)][string]$Message)

    if ([Console]::IsOutputRedirected) {
        [Console]::Error.WriteLine($Message)
        return
    }
    Write-Host $Message
}

$azdValues = azd env get-values --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw 'Could not load the active azd environment.'
}
foreach ($property in $azdValues.PSObject.Properties) {
    Set-Item "Env:$($property.Name)" ([string]$property.Value)
}

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$spaPath = (Resolve-Path (Join-Path $workspaceRoot 'index.html')).Path
$spaUri = [Uri]::new($spaPath).AbsoluteUri
$agentEndpoint = $env:AGENT_TRIAGE_AGENT_RESPONSES_ENDPOINT

if ($agentEndpoint) {
    $encodedEndpoint = [Uri]::EscapeDataString($agentEndpoint)
    $spaUri = "$spaUri`?endpoint=$encodedEndpoint"
}

Write-Host ''
Write-DeploymentResult 'AI Glasses SPA client'
Write-Host "  File: $spaPath"
Write-DeploymentResult "  Open: $spaUri"
Write-Host ''

& "$PSScriptRoot/assign-agent-storage-role.ps1"

Write-Host ''
Write-Host 'Microsoft Foundry Hosted Agents'
$agentNames = @(
    'triage-agent',
    'context-agent',
    'meeting-agent',
    'summarizer-agent',
    'notify-agent'
)
foreach ($agentName in $agentNames) {
    $agent = $null
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        $agentJson = azd ai agent show $agentName --output json 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $agentJson) {
            try {
                $agent = $agentJson | ConvertFrom-Json
                if ($agent.status -eq 'active') {
                    break
                }
            }
            catch {
                $agent = $null
            }
        }
        if ($attempt -lt 12) {
            Start-Sleep -Seconds 10
        }
    }
    if (-not $agent -or $agent.status -ne 'active') {
        throw "Hosted Agent '$agentName' did not become active after deployment."
    }
    Write-Host "  $agentName : version $($agent.version) [$($agent.status)]"
}

$applicationInsights = azd ai connection show ApplicationInsights `
    --output json | ConvertFrom-Json
if (
    $LASTEXITCODE -ne 0 -or
    $applicationInsights.kind -ne 'AppInsights' -or
    -not $env:APPLICATIONINSIGHTS_RESOURCE_ID
) {
    throw 'Application Insights tracing is not connected to the Foundry project.'
}
Write-Host "  Tracing : $($env:APPLICATIONINSIGHTS_RESOURCE_ID)"

if ($env:MAIL_MODE -eq 'live') {
    azd ai toolbox show $env:TOOLBOX_NAME --output json | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Live mail requires the '$($env:TOOLBOX_NAME)' Foundry Toolbox."
    }
    & "$PSScriptRoot/grant-work-iq-mail-consent.ps1"
}
else {
    Write-Warning 'MAIL_MODE is not live; E2E validates the fake mail adapter.'
}

$completed = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        Write-Host "Running deployment E2E test (attempt $attempt/3)..."
        & "$PSScriptRoot/smoke-test.ps1" -IncludeSummarize
        $completed = $true
        break
    }
    catch {
        if ($attempt -eq 3) {
            throw
        }
        Write-Warning "E2E attempt $attempt failed; retrying with a new Hosted Agent session."
    }
}

if (-not $completed) {
    throw 'Deployment E2E test did not complete.'
}

Write-Host ''
Write-DeploymentResult 'Deployment E2E test passed.'
Write-DeploymentResult "SPA client: $spaUri"