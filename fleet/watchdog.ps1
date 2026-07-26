# Poyi Fleet Watchdog
# Keeps the four MCP projects (gateway, watch, foxlink, journal) and their tunnels alive:
#   1. On start (= every boot) re-applies the known-good ACL baseline on each project's
#      ProgramData directory. ACL drift is what caused the 2026-07-26 boot crash loop.
#   2. Starts any fleet service found STOPPED (SCM failure actions never revive a service
#      that failed to launch too many times, and Foxlink shipped without failure actions).
#   3. Force-restarts a service whose process is alive but whose health probe keeps
#      failing (the "degraded" state SCM cannot see), with cooldown and hourly caps.
# Runs as LocalSystem under WinSW (PoyiFleetWatchdog). PowerShell 5.1 compatible.

$ErrorActionPreference = 'Continue'

$script:BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:DataDir = 'C:\ProgramData\Poyi\FleetWatchdog'
$script:LogPath = Join-Path $script:DataDir 'watchdog.log'
$script:MaintenanceFlag = Join-Path $script:DataDir 'maintenance.flag'
$script:EventSource = 'PoyiFleetWatchdog'

New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null

function Write-Log([string]$Level, [string]$Message) {
    $line = '{0} {1} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level.PadRight(5), $Message
    try {
        if ((Test-Path -LiteralPath $script:LogPath) -and
            (Get-Item -LiteralPath $script:LogPath).Length -gt 2MB) {
            Move-Item -LiteralPath $script:LogPath "$script:LogPath.1" -Force
        }
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
    } catch { }
    Write-Output $line
}

function Write-FleetEvent([int]$EventId, [string]$Type, [string]$Message) {
    try {
        Write-EventLog -LogName Application -Source $script:EventSource -EventId $EventId `
            -EntryType $Type -Message $Message
    } catch { }
}

function Get-Config {
    $configPath = Join-Path $script:BaseDir 'fleet-config.json'
    Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
}

function Get-UptimeSeconds {
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    [int]((Get-Date) - $boot).TotalSeconds
}

function Repair-DataDirAcls($Config) {
    foreach ($project in $Config.projects) {
        $dir = $project.dataDir
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        # Additive grants only: never strip existing ACEs, only guarantee the baseline
        # (SYSTEM + Administrators full, each project's service accounts modify).
        & icacls $dir /grant '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T /C /Q 2>$null | Out-Null
        foreach ($principal in $project.grantModify) {
            & icacls $dir /grant "$principal`:(OI)(CI)M" /T /C /Q 2>$null | Out-Null
        }
    }
    Write-Log 'INFO' 'ACL baseline re-applied on all project data directories.'
}

function Get-ServiceState([string]$Name) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $service) { return 'Missing' }
    return [string]$service.Status
}

function Test-Probe([string]$Url, [int]$TimeoutSeconds) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSeconds
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-ServiceStatus([string]$Name, [string]$Status, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ServiceState $Name) -eq $Status) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return ((Get-ServiceState $Name) -eq $Status)
}

function Stop-PortListeners([string]$Url) {
    try {
        $port = ([Uri]$Url).Port
        $owners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ownerPid in $owners) {
            if ($ownerPid -gt 4) {
                Write-Log 'WARN' "Killing leftover listener PID $ownerPid on port $port."
                Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { }
}

# Per-service action bookkeeping: consecutive probe failures, restart budget, backoff.
$script:State = @{}
function Get-TargetState([string]$Name) {
    if (-not $script:State.ContainsKey($Name)) {
        $script:State[$Name] = @{
            Fails = 0; LastRestart = [DateTime]::MinValue
            HourWindow = Get-Date; RestartsInHour = 0; BackoffUntil = [DateTime]::MinValue
        }
    }
    $script:State[$Name]
}

function Test-RestartAllowed($TargetState, $Config) {
    $now = Get-Date
    if ($now -lt $TargetState.BackoffUntil) { return $false }
    if (($now - $TargetState.LastRestart).TotalSeconds -lt $Config.restartCooldownSeconds) { return $false }
    if (($now - $TargetState.HourWindow).TotalMinutes -ge 60) {
        $TargetState.HourWindow = $now
        $TargetState.RestartsInHour = 0
    }
    if ($TargetState.RestartsInHour -ge $Config.maxRestartsPerHour) {
        $TargetState.BackoffUntil = $now.AddSeconds($Config.backoffSeconds)
        Write-Log 'WARN' 'Hourly restart budget exhausted; backing off.'
        Write-FleetEvent 9004 'Warning' 'Hourly restart budget exhausted; backing off.'
        return $false
    }
    return $true
}

function Register-Restart($TargetState) {
    $TargetState.LastRestart = Get-Date
    $TargetState.RestartsInHour = $TargetState.RestartsInHour + 1
    $TargetState.Fails = 0
}

function Start-FleetService([string]$Name, $Config) {
    Write-Log 'INFO' "Starting service $Name."
    & sc.exe start $Name | Out-Null
    if (-not (Wait-ServiceStatus $Name 'Running' 30)) {
        Write-Log 'WARN' "Service $Name did not reach Running; re-applying ACL baseline and retrying once."
        Repair-DataDirAcls $Config
        Start-Sleep -Seconds 5
        & sc.exe start $Name | Out-Null
        if (-not (Wait-ServiceStatus $Name 'Running' 30)) {
            Write-Log 'ERROR' "Service $Name failed to start twice."
            Write-FleetEvent 9005 'Error' "Service $Name failed to start twice."
            return $false
        }
    }
    Write-FleetEvent 9002 'Information' "Service $Name started by watchdog."
    return $true
}

function Restart-McpService($Project, $Config) {
    $mcpName = $Project.mcp.service
    $tunnelName = $Project.tunnel.service
    Write-Log 'WARN' "Force-restarting $mcpName (health probe kept failing)."
    Write-FleetEvent 9003 'Warning' "Force-restarting $mcpName after repeated failed health probes."
    if ((Get-ServiceState $tunnelName) -eq 'Running') {
        & sc.exe stop $tunnelName | Out-Null
        Wait-ServiceStatus $tunnelName 'Stopped' 30 | Out-Null
    }
    & sc.exe stop $mcpName | Out-Null
    if (-not (Wait-ServiceStatus $mcpName 'Stopped' 30)) {
        Write-Log 'WARN' "$mcpName did not stop cleanly; killing its port listener."
    }
    Stop-PortListeners $Project.mcp.health
    Start-FleetService $mcpName $Config | Out-Null
    Start-Sleep -Seconds 3
    Start-FleetService $tunnelName $Config | Out-Null
}

function Restart-TunnelService($Project, $Config) {
    $tunnelName = $Project.tunnel.service
    Write-Log 'WARN' "Force-restarting $tunnelName (readiness probe kept failing)."
    Write-FleetEvent 9003 'Warning' "Force-restarting $tunnelName after repeated failed readiness probes."
    & sc.exe stop $tunnelName | Out-Null
    Wait-ServiceStatus $tunnelName 'Stopped' 30 | Out-Null
    Stop-PortListeners $Project.tunnel.ready
    Start-FleetService $tunnelName $Config | Out-Null
}

function Invoke-FleetPass($Config, [bool]$InBootGrace) {
    foreach ($project in $Config.projects) {
        $mcpName = $project.mcp.service
        $tunnelName = $project.tunnel.service
        $mcpState = Get-ServiceState $mcpName
        $mcpTarget = Get-TargetState $mcpName
        $mcpHealthy = $false

        if ($mcpState -eq 'Missing') {
            Write-Log 'ERROR' "Service $mcpName is not installed."
        } elseif ($mcpState -eq 'Stopped') {
            if (Test-RestartAllowed $mcpTarget $Config) {
                Register-Restart $mcpTarget
                Start-FleetService $mcpName $Config | Out-Null
            }
        } elseif ($mcpState -eq 'Running') {
            $mcpHealthy = Test-Probe $project.mcp.health $Config.probeTimeoutSeconds
            if ($mcpHealthy) {
                $mcpTarget.Fails = 0
            } else {
                $mcpTarget.Fails = $mcpTarget.Fails + 1
                Write-Log 'WARN' ("{0} health probe failed ({1}/{2})." -f
                    $mcpName, $mcpTarget.Fails, $Config.mcpFailThreshold)
                if (-not $InBootGrace -and $mcpTarget.Fails -ge $Config.mcpFailThreshold -and
                    (Test-RestartAllowed $mcpTarget $Config)) {
                    Register-Restart $mcpTarget
                    Restart-McpService $project $Config
                }
            }
        }

        $tunnelState = Get-ServiceState $tunnelName
        $tunnelTarget = Get-TargetState $tunnelName
        if ($tunnelState -eq 'Missing') {
            Write-Log 'ERROR' "Service $tunnelName is not installed."
            continue
        }
        if ($tunnelState -eq 'Stopped') {
            # A stopped tunnel whose ready port is still bound means an orphaned
            # tunnel-client survived a service kill; remove it before restarting.
            if (Test-RestartAllowed $tunnelTarget $Config) {
                Register-Restart $tunnelTarget
                Stop-PortListeners $project.tunnel.ready
                Start-FleetService $tunnelName $Config | Out-Null
            }
            continue
        }
        if ($tunnelState -ne 'Running') { continue }
        if (-not $mcpHealthy) {
            # Tunnel readiness legitimately fails while its MCP is down; do not punish it.
            $tunnelTarget.Fails = 0
            continue
        }
        if (Test-Probe $project.tunnel.ready $Config.probeTimeoutSeconds) {
            $tunnelTarget.Fails = 0
        } else {
            $tunnelTarget.Fails = $tunnelTarget.Fails + 1
            Write-Log 'WARN' ("{0} readiness probe failed ({1}/{2})." -f
                $tunnelName, $tunnelTarget.Fails, $Config.tunnelFailThreshold)
            if (-not $InBootGrace -and $tunnelTarget.Fails -ge $Config.tunnelFailThreshold -and
                (Test-RestartAllowed $tunnelTarget $Config)) {
                Register-Restart $tunnelTarget
                Restart-TunnelService $project $Config
            }
        }
    }
}

# --- main ---
$config = Get-Config
Write-Log 'INFO' ("Fleet watchdog starting; monitoring {0} projects." -f @($config.projects).Count)
Write-FleetEvent 9001 'Information' 'Fleet watchdog started.'
Repair-DataDirAcls $config

while ($true) {
    try {
        if (Test-Path -LiteralPath $script:MaintenanceFlag) {
            Write-Log 'INFO' 'maintenance.flag present; skipping this pass.'
        } else {
            $inBootGrace = (Get-UptimeSeconds) -lt $config.bootGraceSeconds
            Invoke-FleetPass $config $inBootGrace
        }
    } catch {
        Write-Log 'ERROR' ("Watchdog pass failed: {0}" -f $_.Exception.Message)
    }
    Start-Sleep -Seconds $config.loopSeconds
}
