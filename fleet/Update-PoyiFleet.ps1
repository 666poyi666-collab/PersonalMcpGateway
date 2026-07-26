# Redeploys gateway code and watchdog from this repo checkout. Run elevated.
#   1. Builds the gateway wheel with uv and installs it into the service's
#      private Python (offline: no downloads, dependencies stay locked).
#   2. Restarts the gateway and its tunnel.
#   3. Refreshes the watchdog files and restarts PoyiFleetWatchdog.
# Idempotent; safe to run whenever fleet/ or src/ changed.
[CmdletBinding()]
param([switch]$Pause)

$ErrorActionPreference = 'Stop'
$fleetSource = $PSScriptRoot
$repoRoot = Split-Path -Parent $fleetSource
$gatewayInstall = "$env:ProgramFiles\Poyi\PersonalMcpGateway"
$watchdogInstall = "$env:ProgramFiles\Poyi\FleetWatchdog"
$diagDir = 'C:\开发\mcp开发\_diag'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run Update-PoyiFleet.ps1 from an elevated PowerShell session.'
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

function Wait-Endpoint([string]$Uri, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

Assert-Administrator
New-Item -ItemType Directory -Path $diagDir -Force | Out-Null
Start-Transcript -Path (Join-Path $diagDir 'fleet-update.log') -Force | Out-Null
# Pause the watchdog while services are intentionally down, or it will race the
# wheel installation by restarting the gateway mid-copy.
$maintenanceFlag = 'C:\ProgramData\Poyi\FleetWatchdog\maintenance.flag'
New-Item -ItemType Directory -Path (Split-Path $maintenanceFlag) -Force | Out-Null
Set-Content -LiteralPath $maintenanceFlag -Value 'fleet update in progress' -Encoding ASCII
try {
    $uv = (Get-Command uv -ErrorAction Stop).Source
    $pythonDirs = @(Get-ChildItem -LiteralPath (Join-Path $gatewayInstall 'python') -Directory |
        Where-Object { $_.Name -like 'cpython-3.12.*' } | Sort-Object Name -Descending)
    if ($pythonDirs.Count -lt 1) { throw 'Private Python runtime not found.' }
    $python = Join-Path $pythonDirs[0].FullName 'python.exe'

    Write-Host '== 1/3 Build and install the gateway wheel ==' -ForegroundColor Cyan
    $buildDir = Join-Path $env:TEMP ('poyi-fleet-update-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $buildDir | Out-Null
    try {
        Push-Location $repoRoot
        try {
            & $uv build --wheel --out-dir $buildDir
            if ($LASTEXITCODE -ne 0) { throw 'uv build failed.' }
        } finally { Pop-Location }
        $wheels = @(Get-ChildItem -LiteralPath $buildDir -Filter '*.whl' -File)
        if ($wheels.Count -ne 1) { throw 'Expected exactly one wheel.' }

        Write-Host '  stopping gateway services...'
        & sc.exe stop OpenAISecureMcpTunnel | Out-Null
        if (-not (Wait-ServiceStatus 'OpenAISecureMcpTunnel' 'Stopped' 40)) {
            throw 'OpenAISecureMcpTunnel did not stop; aborting update.'
        }
        # Only now can the gateway stop: SCM refuses to stop a service whose
        # dependents are still running, and that refusal must not be ignored.
        & sc.exe stop PoyiPersonalMcpGateway | Out-Null
        if (-not (Wait-ServiceStatus 'PoyiPersonalMcpGateway' 'Stopped' 40)) {
            throw 'PoyiPersonalMcpGateway did not stop; aborting update.'
        }

        & $uv pip install --python $python --break-system-packages --no-deps `
            --reinstall $wheels[0].FullName
        if ($LASTEXITCODE -ne 0) { throw 'Wheel installation failed.' }
        # pip moves files in from %TEMP%, which strips the service accounts'
        # inherited read ACEs (that exact drift crash-looped the gateway on
        # 2026-07-26). Re-stamp the install tree after every wheel install.
        & icacls $gatewayInstall /grant 'NT SERVICE\PoyiPersonalMcpGateway:(OI)(CI)RX' `
            'NT SERVICE\OpenAISecureMcpTunnel:(OI)(CI)RX' 'BUILTIN\Users:(OI)(CI)RX' /T /C /Q | Out-Null
        Write-Host '  service read ACLs re-stamped on the install tree.'
    } finally {
        Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host '== 2/3 Restart gateway and tunnel ==' -ForegroundColor Cyan
    & sc.exe start PoyiPersonalMcpGateway | Out-Null
    if (-not (Wait-ServiceStatus 'PoyiPersonalMcpGateway' 'Running' 40)) {
        throw 'Gateway did not reach Running after update.'
    }
    if (-not (Wait-Endpoint 'http://127.0.0.1:8761/healthz' 60)) {
        throw 'Gateway health endpoint did not come back after update.'
    }
    & sc.exe start OpenAISecureMcpTunnel | Out-Null
    Write-Host '  gateway back online.'

    Write-Host '== 3/3 Refresh watchdog ==' -ForegroundColor Cyan
    Copy-Item (Join-Path $fleetSource 'watchdog.ps1') $watchdogInstall -Force
    Copy-Item (Join-Path $fleetSource 'fleet-config.json') $watchdogInstall -Force
    Copy-Item (Join-Path $fleetSource 'PoyiFleetWatchdog.xml') $watchdogInstall -Force
    & sc.exe stop PoyiFleetWatchdog | Out-Null
    Wait-ServiceStatus 'PoyiFleetWatchdog' 'Stopped' 30 | Out-Null
    & sc.exe start PoyiFleetWatchdog | Out-Null
    Wait-ServiceStatus 'PoyiFleetWatchdog' 'Running' 30 | Out-Null
    Write-Host 'Fleet update finished.' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $maintenanceFlag -Force -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
if ($Pause) { Read-Host '按回车关闭窗口' | Out-Null }
