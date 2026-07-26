# Installs / repairs the Poyi MCP fleet reliability layer. Idempotent; run elevated.
#   1. Re-applies the ACL baseline on every project's ProgramData directory.
#   2. Writes the corrected dashboard-targets.yaml (true tunnel port map).
#   3. Hardens all fleet services: delayed auto start + infinite failure restarts.
#   4. Installs and starts the PoyiFleetWatchdog service.
#   5. Starts anything currently stopped and prints a final probe report.
[CmdletBinding()]
param([switch]$Pause)

$ErrorActionPreference = 'Continue'
$fleetSource = $PSScriptRoot
$installDir = "$env:ProgramFiles\Poyi\FleetWatchdog"
$dataDir = "$env:ProgramData\Poyi\FleetWatchdog"
$diagDir = 'C:\开发\mcp开发\_diag'
$gatewayInstall = "$env:ProgramFiles\Poyi\PersonalMcpGateway"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run Install-PoyiFleet.ps1 from an elevated PowerShell session.'
    }
}

function Wait-ServiceStatus([string]$Name, [string]$Status, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -ne $service -and [string]$service.Status -eq $Status) { return $true }
        Start-Sleep -Milliseconds 500
    }
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    return ($null -ne $service -and [string]$service.Status -eq $Status)
}

Assert-Administrator
New-Item -ItemType Directory -Path $installDir, $dataDir, $diagDir -Force | Out-Null
Start-Transcript -Path (Join-Path $diagDir 'fleet-install.log') -Force | Out-Null

try {
    $config = Get-Content -Raw -LiteralPath (Join-Path $fleetSource 'fleet-config.json') |
        ConvertFrom-Json

    Write-Host '== 1/5 ACL baseline on project data directories ==' -ForegroundColor Cyan
    foreach ($project in $config.projects) {
        $dir = $project.dataDir
        if (-not (Test-Path -LiteralPath $dir)) {
            Write-Warning "Data dir missing: $dir"
            continue
        }
        & icacls $dir /grant '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T /C /Q | Out-Null
        foreach ($principal in $project.grantModify) {
            & icacls $dir /grant "$principal`:(OI)(CI)M" /T /C /Q | Out-Null
        }
        Write-Host "  baseline applied: $dir"
    }
    & icacls $dataDir /grant '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T /C /Q | Out-Null

    Write-Host '== 2/5 Correct dashboard tunnel port map ==' -ForegroundColor Cyan
    $targetsPath = 'C:\ProgramData\Poyi\PersonalMcpGateway\dashboard-targets.yaml'
    if ((Test-Path -LiteralPath $targetsPath) -and
        -not (Test-Path -LiteralPath "$targetsPath.bak")) {
        Copy-Item -LiteralPath $targetsPath "$targetsPath.bak" -Force
    }
    @'
# Poyi fleet: corrected tunnel readiness ports (was shuffled across three projects).
# watch tunnel listens on 8880, foxlink on 8878, journal on 8887 - verified against
# each running tunnel-client's /api/status on 2026-07-26. Full entries: an entry here
# replaces the built-in default with the same id.
targets:
  - id: watch
    name: Watch MCP
    description: 训练、睡眠与手表控制
    icon: watch
    accent: "#2dd4bf"
    health_url: http://127.0.0.1:8768/healthz
    ready_url: http://127.0.0.1:8768/readyz
    tunnel_ready_url: http://127.0.0.1:8880/readyz
  - id: foxlink
    name: Foxlink MCP
    description: Foxlink 数据与业务能力
    icon: link
    accent: "#fb923c"
    health_url: http://127.0.0.1:8770/healthz
    ready_url: http://127.0.0.1:8770/readyz
    tunnel_ready_url: http://127.0.0.1:8878/readyz
  - id: journal
    name: Journal MCP
    description: 日记、复盘与个人记录
    icon: journal
    accent: "#f472b6"
    health_url: http://127.0.0.1:8780/healthz
    ready_url: http://127.0.0.1:8780/readyz
    tunnel_ready_url: http://127.0.0.1:8887/readyz
'@ | Set-Content -LiteralPath $targetsPath -Encoding UTF8
    & icacls $targetsPath /grant '*S-1-5-32-544:F' 'NT SERVICE\PoyiPersonalMcpGateway:R' | Out-Null
    Write-Host "  wrote $targetsPath"

    Write-Host '== 3/5 Harden service start + failure actions ==' -ForegroundColor Cyan
    $fleetServices = @()
    foreach ($project in $config.projects) {
        $fleetServices += $project.mcp.service
        $fleetServices += $project.tunnel.service
    }
    foreach ($name in $fleetServices) {
        if ($null -eq (Get-Service -Name $name -ErrorAction SilentlyContinue)) {
            Write-Warning "Service not installed, skipping: $name"
            continue
        }
        & sc.exe config $name start= delayed-auto | Out-Null
        & sc.exe failure $name reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
        & sc.exe failureflag $name 1 | Out-Null
        Write-Host "  hardened: $name"
    }

    Write-Host '== 4/5 Install PoyiFleetWatchdog service ==' -ForegroundColor Cyan
    Copy-Item (Join-Path $fleetSource 'watchdog.ps1') $installDir -Force
    Copy-Item (Join-Path $fleetSource 'fleet-config.json') $installDir -Force
    Copy-Item (Join-Path $fleetSource 'PoyiFleetWatchdog.xml') $installDir -Force
    $watchdogExe = Join-Path $installDir 'PoyiFleetWatchdog.exe'
    $winswSource = Join-Path $gatewayInstall 'PoyiPersonalMcpGateway.exe'
    if (-not (Test-Path -LiteralPath $watchdogExe)) {
        if (-not (Test-Path -LiteralPath $winswSource)) {
            throw "WinSW source binary missing: $winswSource"
        }
        Copy-Item -LiteralPath $winswSource $watchdogExe -Force
    }
    if ([System.Diagnostics.EventLog]::SourceExists('PoyiFleetWatchdog') -eq $false) {
        New-EventLog -LogName Application -Source 'PoyiFleetWatchdog'
    }
    $existing = Get-Service -Name 'PoyiFleetWatchdog' -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Status -ne 'Stopped') {
            & sc.exe stop PoyiFleetWatchdog | Out-Null
            Wait-ServiceStatus 'PoyiFleetWatchdog' 'Stopped' 30 | Out-Null
        }
        & $watchdogExe uninstall | Out-Null
    }
    & $watchdogExe install
    if ($LASTEXITCODE -ne 0) { throw 'Watchdog service installation failed.' }
    & sc.exe failure PoyiFleetWatchdog reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
    & sc.exe failureflag PoyiFleetWatchdog 1 | Out-Null
    & $watchdogExe start | Out-Null
    if (Wait-ServiceStatus 'PoyiFleetWatchdog' 'Running' 30) {
        Write-Host '  PoyiFleetWatchdog is running.'
    } else {
        Write-Warning '  PoyiFleetWatchdog did not reach Running.'
    }

    Write-Host '== 5/5 Start stopped services and probe the fleet ==' -ForegroundColor Cyan
    foreach ($project in $config.projects) {
        foreach ($kind in @('mcp', 'tunnel')) {
            $name = $project.$kind.service
            $service = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($null -eq $service) { continue }
            if ($service.Status -eq 'Stopped') {
                Write-Host "  starting $name..."
                & sc.exe start $name | Out-Null
                if (-not (Wait-ServiceStatus $name 'Running' 40)) {
                    Write-Warning "  $name failed to start; collecting evidence."
                    Get-WinEvent -FilterHashtable @{LogName = 'Application'; StartTime = (Get-Date).AddMinutes(-10) } `
                        -MaxEvents 60 -ErrorAction SilentlyContinue |
                        Where-Object { $_.ProviderName -match [regex]::Escape($name) } |
                        Select-Object TimeCreated, ProviderName, Message |
                        Out-File (Join-Path $diagDir "$name-start-failure.txt") -Encoding utf8
                }
            }
        }
    }

    Start-Sleep -Seconds 8
    $report = @()
    foreach ($project in $config.projects) {
        foreach ($kind in @('mcp', 'tunnel')) {
            $name = $project.$kind.service
            $url = $null
            if ($kind -eq 'mcp') { $url = $project.mcp.health } else { $url = $project.tunnel.ready }
            $service = Get-Service -Name $name -ErrorAction SilentlyContinue
            $state = 'Missing'
            if ($null -ne $service) { $state = [string]$service.Status }
            $probe = 'FAIL'
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
                if ($response.StatusCode -eq 200) { $probe = 'OK' }
            } catch { }
            $report += [pscustomobject]@{
                Project = $project.id; Kind = $kind; Service = $name
                State = $state; Probe = $probe; Url = $url
            }
        }
    }
    $report | Format-Table -AutoSize | Out-String | Write-Host
    $report | ConvertTo-Json | Out-File (Join-Path $diagDir 'fleet-probe.json') -Encoding utf8
    Write-Host 'Fleet install/repair finished.' -ForegroundColor Green
} finally {
    Stop-Transcript | Out-Null
}
if ($Pause) { Read-Host '按回车关闭窗口' | Out-Null }
