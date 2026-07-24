param(
    [switch]$IncludeSummarize
)

$ErrorActionPreference = 'Stop'

$analyzeRequestPath = [System.IO.Path]::GetTempFileName()
$summarizeRequestPath = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$meetingFixturePath = Join-Path $PSScriptRoot '..\data\meeting-room.md'
$meetingFixture = [System.IO.File]::ReadAllText(
    (Resolve-Path $meetingFixturePath),
    $utf8NoBom
)
$meetingLines = $meetingFixture -split "`r?`n"
$meetingTitle = ($meetingLines[0] -replace '^#\s*', '')
$meetingRoom = ($meetingLines[3] -replace '^- \*\*.*\*\*: ', '')
$analyzeRequest = @{
    tool = 'analyze_room'
    ocr_texts = @($meetingRoom, $meetingTitle)
} | ConvertTo-Json -Depth 3 -Compress

try {
    [System.IO.File]::WriteAllText($analyzeRequestPath, $analyzeRequest, $utf8NoBom)
    $analyzeOutput = azd ai agent invoke triage-agent `
        --new-session `
        --input-file $analyzeRequestPath 2>&1 | Out-String
    $analyzeExitCode = $LASTEXITCODE
    Write-Host $analyzeOutput
    $roomPattern = '"room"\s*:\s*"' + [Regex]::Escape($meetingRoom) + '"'
    if ($analyzeExitCode -ne 0 -or $analyzeOutput -notmatch $roomPattern) {
        throw 'analyze_room smoke test failed.'
    }

    if ($IncludeSummarize) {
        $summarizeRequest = @{
            tool = 'summarize_meeting'
            transcript = $meetingFixture
            meeting = @{
                title = $meetingTitle
                attendees = 5
            }
            frames = @()
        } | ConvertTo-Json -Depth 5 -Compress
        [System.IO.File]::WriteAllText($summarizeRequestPath, $summarizeRequest, $utf8NoBom)
        $summarizeOutput = azd ai agent invoke triage-agent `
            --new-session `
            --input-file $summarizeRequestPath 2>&1 | Out-String
        $summarizeExitCode = $LASTEXITCODE
        Write-Host $summarizeOutput
        if ($summarizeExitCode -ne 0 -or $summarizeOutput -notmatch '"done"\s*:\s*true') {
            throw 'summarize_meeting smoke test failed.'
        }
    }
}
finally {
    Remove-Item $analyzeRequestPath, $summarizeRequestPath -Force -ErrorAction SilentlyContinue
}