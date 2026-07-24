$ErrorActionPreference = 'Stop'

$toolboxName = 'agent-tools'
azd ai toolbox show $toolboxName --output json | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Foundry Toolbox '$toolboxName' was not found. Complete docs/work-iq-setup.md first."
}

& "$PSScriptRoot/grant-work-iq-mail-consent.ps1"

azd env set TOOLBOX_NAME $toolboxName | Out-Null
azd env set MAIL_MODE live | Out-Null
azd deploy notify-agent --no-prompt

Write-Host 'Live Outlook Mail mode is enabled for notify-agent.'