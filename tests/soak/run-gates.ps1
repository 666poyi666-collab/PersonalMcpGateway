[CmdletBinding()]
param(
    [ValidateSet('status-1000', 'recovery-100', 'gateway-restart-20',
        'tunnel-restart-20', 'active-24h', 'idle-72h')]
    [string]$Gate,
    [string]$EvidenceDir = "$PSScriptRoot\..\..\evidence",
    [string]$AdminBaseUrl = 'http://127.0.0.1:8761'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
$path = Join-Path $EvidenceDir "$Gate.json"
$started = [DateTimeOffset]::UtcNow
$checks = 0

function Test-Ready {
    $response = Invoke-RestMethod "$AdminBaseUrl/readyz" -TimeoutSec 5
    if ($response.gateway -ne 'ready') { throw 'Gateway is not ready.' }
    $script:checks++
}

function Write-Evidence([string]$Status, [string]$Message) {
    [ordered]@{
        gate = $Gate
        status = $Status
        startedAt = $started.ToString('o')
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        checks = $script:checks
        message = $Message
    } | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
}

try {
    switch ($Gate) {
        'status-1000' { 1..1000 | ForEach-Object { Test-Ready } }
        'recovery-100' { 1..100 | ForEach-Object { Test-Ready; Start-Sleep -Milliseconds 100 } }
        'gateway-restart-20' {
            1..20 | ForEach-Object { Restart-Service PoyiPersonalMcpGateway; Test-Ready }
        }
        'tunnel-restart-20' {
            1..20 | ForEach-Object { Restart-Service OpenAISecureMcpTunnel; Test-Ready }
        }
        'active-24h' {
            $deadline = $started.AddHours(24)
            while ([DateTimeOffset]::UtcNow -lt $deadline) {
                Test-Ready
                Write-Evidence 'running' 'Active gate has not reached 24 hours.'
                Start-Sleep -Seconds 60
            }
        }
        'idle-72h' {
            $deadline = $started.AddHours(72)
            while ([DateTimeOffset]::UtcNow -lt $deadline) {
                Test-Ready
                Write-Evidence 'running' 'Idle gate has not reached 72 hours.'
                Start-Sleep -Seconds 900
            }
        }
    }
    Write-Evidence 'passed' 'Gate completed its full configured count or duration.'
} catch {
    Write-Evidence 'failed' $_.Exception.Message
    throw
}
