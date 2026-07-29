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

function Set-BoundedFailureActions([string]$Name) {
    & sc.exe failure $Name reset= 3600 `
        actions= restart/5000/restart/15000/restart/60000/none/0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to cap SCM recovery actions for $Name." }
    & sc.exe failureflag $Name 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to enable SCM recovery actions for $Name." }
}

function Copy-VerifiedFile([string]$Source, [string]$Destination) {
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash
    $destinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "Installed file hash mismatch: $Destination"
    }
}

function Assert-ServiceRuntimeReadAccess([string]$Root, [string]$Principal) {
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $principalSid = ([Security.Principal.NTAccount]$Principal).Translate(
        [Security.Principal.SecurityIdentifier]).Value
    $requiredRights = [Security.AccessControl.FileSystemRights]::ReadAndExecute
    $patterns = @(
        'python\cpython-3.12.*\python.exe',
        'python\cpython-3.12.*\Lib\site-packages\personal_mcp_gateway\service_bootstrap.py',
        'python\cpython-3.12.*\Lib\site-packages\uvicorn\main.py',
        'python\cpython-3.12.*\Lib\site-packages\uvicorn\supervisors\statreload.py',
        'python\cpython-3.12.*\Lib\site-packages\watchfiles\_rust_notify*.pyd'
    )
    foreach ($pattern in $patterns) {
        $matches = @(Get-ChildItem -Path (Join-Path $Root $pattern) -File `
                -ErrorAction SilentlyContinue)
        if ($matches.Count -lt 1) { throw "Runtime ACL verification file missing: $pattern" }
        foreach ($file in $matches) {
            $resolved = [IO.Path]::GetFullPath($file.FullName)
            if (-not $resolved.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Runtime ACL verification escaped install root: $resolved"
            }
            $allowed = $false
            $denied = $false
            foreach ($rule in (Get-Acl -LiteralPath $resolved).Access) {
                try {
                    $ruleSid = $rule.IdentityReference.Translate(
                        [Security.Principal.SecurityIdentifier]).Value
                } catch { continue }
                if ($ruleSid -ne $principalSid) { continue }
                $rights = $rule.FileSystemRights -band $requiredRights
                if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny) {
                    if ($rights -ne 0) { $denied = $true }
                } elseif ($rights -eq $requiredRights) {
                    $allowed = $true
                }
            }
            if ($denied -or -not $allowed) {
                throw "Service runtime read verification failed: $resolved"
            }
        }
    }
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
        & icacls $gatewayInstall /grant:r 'NT SERVICE\PoyiPersonalMcpGateway:(OI)(CI)RX' `
            'NT SERVICE\OpenAISecureMcpTunnel:(OI)(CI)RX' 'BUILTIN\Users:(OI)(CI)RX' /T /C /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to re-stamp service runtime ACLs.' }
        Assert-ServiceRuntimeReadAccess $gatewayInstall 'NT SERVICE\PoyiPersonalMcpGateway'
        Write-Host '  service read ACLs re-stamped on the install tree.'
    } finally {
        Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host '== 2/3 Restart gateway and tunnel ==' -ForegroundColor Cyan
    & sc.exe start PoyiPersonalMcpGateway | Out-Null
    if (-not (Wait-ServiceStatus 'PoyiPersonalMcpGateway' 'Running' 40)) {
        throw 'Gateway did not reach Running after update.'
    }
    if (-not (Wait-Endpoint 'http://127.0.0.1:8761/readyz' 60)) {
        throw 'Gateway readiness endpoint did not come back after update.'
    }
    & sc.exe start OpenAISecureMcpTunnel | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1056) {
        throw 'OpenAISecureMcpTunnel failed to start after update.'
    }
    if (-not (Wait-ServiceStatus 'OpenAISecureMcpTunnel' 'Running' 40)) {
        throw 'OpenAISecureMcpTunnel did not remain running after update.'
    }
    if (-not (Wait-Endpoint 'http://127.0.0.1:8877/readyz' 60)) {
        throw 'Tunnel readiness endpoint did not come back after update.'
    }
    Set-BoundedFailureActions 'PoyiPersonalMcpGateway'
    Set-BoundedFailureActions 'OpenAISecureMcpTunnel'
    Write-Host '  gateway back online.'

    Write-Host '== 3/3 Refresh watchdog ==' -ForegroundColor Cyan
    Copy-VerifiedFile (Join-Path $fleetSource 'watchdog.ps1') `
        (Join-Path $watchdogInstall 'watchdog.ps1')
    Copy-VerifiedFile (Join-Path $fleetSource 'fleet-config.json') `
        (Join-Path $watchdogInstall 'fleet-config.json')
    Copy-VerifiedFile (Join-Path $fleetSource 'PoyiFleetWatchdog.xml') `
        (Join-Path $watchdogInstall 'PoyiFleetWatchdog.xml')
    Copy-VerifiedFile (Join-Path $fleetSource 'cloud_sync.py') `
        (Join-Path $watchdogInstall 'cloud_sync.py')
    & sc.exe stop PoyiFleetWatchdog | Out-Null
    Wait-ServiceStatus 'PoyiFleetWatchdog' 'Stopped' 30 | Out-Null
    & sc.exe start PoyiFleetWatchdog | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1056) {
        throw 'PoyiFleetWatchdog failed to start after update.'
    }
    if (-not (Wait-ServiceStatus 'PoyiFleetWatchdog' 'Running' 30)) {
        throw 'PoyiFleetWatchdog did not remain running after update.'
    }
    Set-BoundedFailureActions 'PoyiFleetWatchdog'
    Write-Host 'Fleet update finished.' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $maintenanceFlag -Force -ErrorAction SilentlyContinue
    Stop-Transcript | Out-Null
}
if ($Pause) { Read-Host '按回车关闭窗口' | Out-Null }
