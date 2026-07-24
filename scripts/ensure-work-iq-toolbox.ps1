$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$connectionName = 'WorkIQMail'
$toolboxName = 'agent-tools'
$mailEndpoint = 'https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools'
$agentToolsAudience = 'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1'
$catalogEntityId = 'azureml://location/eastus/apiCenter/registry-prod-bl/type/tools/objectId/microsoft-outlook-mail-mcp-frontier/version/1'

function Test-AzdCommand([string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & azd @Arguments 2>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if (-not (Test-AzdCommand @('ai', 'connection', 'show', $connectionName, '--output', 'json'))) {
    azd ai connection create $connectionName `
        --kind remote-tool `
        --target $mailEndpoint `
        --auth-type user-entra-token `
        --audience $agentToolsAudience `
        --metadata 'oAuthProvider=managed' `
        --metadata "toolEntityId=$catalogEntityId" `
        --metadata 'type=catalog_MCP' `
        --no-prompt | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the $connectionName Foundry connection."
    }
    Write-Host "Created Foundry connection $connectionName."
}
else {
    Write-Host "Foundry connection $connectionName already exists."
}

if (-not (Test-AzdCommand @('ai', 'toolbox', 'show', $toolboxName, '--output', 'json'))) {
    $definitionFile = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        "$([Guid]::NewGuid().ToString('N')).json"
    try {
        $definition = @{
            description = 'AI Glasses meeting summary delivery through Outlook Mail'
            connections = @(@{ name = $connectionName })
        } | ConvertTo-Json -Depth 4
        [System.IO.File]::WriteAllText(
            $definitionFile,
            $definition,
            [System.Text.UTF8Encoding]::new($false)
        )
        $createOutput = azd ai toolbox create $toolboxName `
            --from-file $definitionFile `
            --output json `
            --no-prompt 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the $toolboxName Foundry Toolbox. $createOutput"
        }
    }
    finally {
        Remove-Item $definitionFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Created and published Foundry Toolbox $toolboxName."
}
else {
    Write-Host "Foundry Toolbox $toolboxName already exists."
}

azd env set TOOLBOX_NAME $toolboxName | Out-Null
azd env set MAIL_MODE live | Out-Null
Write-Host 'Configured azd environment for live Work IQ Mail.'
