$ErrorActionPreference = 'Stop'

azd env get-values | ForEach-Object {
    if ($_ -match '^([A-Z0-9_]+)="(.*)"$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

$env:STORAGE_MODE = 'fake'
$env:MAIL_MODE = 'fake'
$env:LOCAL_ARTIFACTS_DIR = (Join-Path $PSScriptRoot '..\artifacts')

& ${env:PYTHON_EXECUTABLE} -m debugpy --listen 127.0.0.1:5679 (Join-Path $PSScriptRoot '..\src\meeting-agent\main.py')