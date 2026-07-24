$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

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

if (-not $agentEndpoint -and $env:FOUNDRY_PROJECT_ENDPOINT) {
    $agentEndpoint = "$($env:FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/'))/agents/triage-agent/endpoint/protocols/openai/responses?api-version=v1"
}
if ($agentEndpoint) {
    $spaUri = "$spaUri`?endpoint=$([Uri]::EscapeDataString($agentEndpoint))"
}

$messages = @(
    '',
    'AI Glasses SPA client',
    "  File: $spaPath",
    "  Open: $spaUri",
    ''
)
foreach ($message in $messages) {
    if ([Console]::IsOutputRedirected) {
        [Console]::Error.WriteLine($message)
        continue
    }
    Write-Host $message
}