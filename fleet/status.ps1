# Poyi fleet status: service states + health probes. No elevation required.
$ErrorActionPreference = 'Continue'
$configPath = Join-Path $PSScriptRoot 'fleet-config.json'
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json

$rows = @()
foreach ($project in $config.projects) {
    foreach ($kind in @('mcp', 'tunnel')) {
        $name = $project.$kind.service
        if ($kind -eq 'mcp') { $url = $project.mcp.health } else { $url = $project.tunnel.ready }
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        $state = 'Missing'
        if ($null -ne $service) { $state = [string]$service.Status }
        $probe = 'FAIL'
        $ms = ''
        try {
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
            $sw.Stop()
            if ($response.StatusCode -eq 200) {
                $probe = 'OK'
                $ms = [string][int]$sw.ElapsedMilliseconds + 'ms'
            }
        } catch { }
        $rows += [pscustomobject]@{
            项目 = $project.name; 部件 = $kind; 服务 = $name; 服务状态 = $state
            探测 = $probe; 延迟 = $ms
        }
    }
}
$watchdog = Get-Service -Name 'PoyiFleetWatchdog' -ErrorAction SilentlyContinue
$watchdogState = 'Missing (先运行 Repair-PoyiFleet.cmd)'
if ($null -ne $watchdog) { $watchdogState = [string]$watchdog.Status }

Write-Host ''
Write-Host ('看护服务 PoyiFleetWatchdog: ' + $watchdogState)
$rows | Format-Table -AutoSize
$bad = @($rows | Where-Object { $_.服务状态 -ne 'Running' -or $_.探测 -ne 'OK' })
if ($bad.Count -eq 0) {
    Write-Host '全部 8 个服务在线且探测通过。' -ForegroundColor Green
} else {
    Write-Host ('异常部件 ' + $bad.Count + ' 个；watchdog 会自动处理，也可运行 Repair-PoyiFleet.cmd 立即修复。') `
        -ForegroundColor Yellow
}
