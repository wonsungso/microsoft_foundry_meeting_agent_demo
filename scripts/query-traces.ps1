param(
    [ValidateRange(1, 1440)]
    [int]$Minutes = 30,
    [string]$TraceId
)

$ErrorActionPreference = 'Stop'

$azdValues = azd env get-values --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $azdValues.LOG_ANALYTICS_WORKSPACE_RESOURCE_ID) {
    throw 'The active azd environment does not contain a Log Analytics workspace.'
}

$workspaceId = az monitor log-analytics workspace show `
    --ids $azdValues.LOG_ANALYTICS_WORKSPACE_RESOURCE_ID `
    --query customerId `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $workspaceId) {
    throw 'Could not resolve the Log Analytics workspace customer ID.'
}

$queryParts = @(
    'union isfuzzy=true withsource=TableName AppRequests, AppDependencies, AppTraces'
    "| where TimeGenerated > ago($($Minutes)m)"
)
if ($TraceId) {
    $queryParts += "| where OperationId == '$TraceId'"
}
$queryParts += @(
    "| extend AgentName = tostring(parse_json(Properties)['gen_ai.agent.name'])"
    "| where AgentName in ('triage-agent', 'context-agent', 'meeting-agent', 'summarizer-agent', 'notify-agent')"
    '| summarize Events=count(), FirstSeen=min(TimeGenerated), LastSeen=max(TimeGenerated) by OperationId, AgentName, TableName'
    '| order by FirstSeen asc'
)
$query = $queryParts -join ' '

$resultJson = az monitor log-analytics query `
    --workspace $workspaceId `
    --analytics-query $query `
    --output json
if ($LASTEXITCODE -ne 0) {
    throw 'Log Analytics trace query failed.'
}

$agentNames = @('triage-agent', 'context-agent', 'meeting-agent', 'summarizer-agent', 'notify-agent')
$events = foreach ($row in ($resultJson | ConvertFrom-Json)) {
    $properties = if ($row.Properties -is [string]) {
        $row.Properties | ConvertFrom-Json
    }
    else {
        $row.Properties
    }
    $agentName = if ($row.AgentName) { $row.AgentName } else { $properties.'gen_ai.agent.name' }
    if ($agentName -in $agentNames) {
        [pscustomobject]@{
            OperationId = $row.OperationId
            AgentName   = $agentName
            TableName   = $row.TableName
            Events      = if ($row.Events) { [int]$row.Events } else { 1 }
            FirstSeen   = if ($row.FirstSeen) { $row.FirstSeen } else { $row.TimeGenerated }
            LastSeen    = if ($row.LastSeen) { $row.LastSeen } else { $row.TimeGenerated }
        }
    }
}

$events |
    Group-Object OperationId, AgentName, TableName |
    ForEach-Object {
        [pscustomobject]@{
            OperationId = $_.Group[0].OperationId
            AgentName   = $_.Group[0].AgentName
            TableName   = $_.Group[0].TableName
            Events      = ($_.Group.Events | Measure-Object -Sum).Sum
            FirstSeen   = ($_.Group.FirstSeen | Sort-Object | Select-Object -First 1)
            LastSeen    = ($_.Group.LastSeen | Sort-Object | Select-Object -Last 1)
        }
    } |
    Sort-Object FirstSeen |
    Format-Table -AutoSize