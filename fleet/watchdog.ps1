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
$script:TriggerDir = Join-Path $script:DataDir 'triggers'
$script:EventSource = 'PoyiFleetWatchdog'
$script:LastForcedPass = [DateTime]::MinValue
$script:RestartStatePath = Join-Path $script:DataDir 'restart-state.json'
$script:PersistedRestartAttempts = @{}

New-Item -ItemType Directory -Path $script:DataDir, $script:TriggerDir -Force | Out-Null

function Write-Log([string]$Level, [string]$Message) {
    $line = '{0} {1} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level.PadRight(5), $Message
    try {
        if ((Test-Path -LiteralPath $script:LogPath) -and
            (Get-Item -LiteralPath $script:LogPath).Length -gt 2MB) {
            Move-Item -LiteralPath $script:LogPath "$script:LogPath.1" -Force
        }
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
    } catch { }
    Write-Host $line
}

function Write-FleetEvent([int]$EventId, [string]$Type, [string]$Message) {
    try {
        Write-EventLog -LogName Application -Source $script:EventSource -EventId $EventId `
            -EntryType $Type -Message $Message
    } catch { }
}

function Get-Config {
    $configPath = Join-Path $script:BaseDir 'fleet-config.json'
    $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    if ($config.schemaVersion -ne 1 -or @($config.projects).Count -lt 1) {
        throw 'Unsupported or empty fleet configuration.'
    }
    $ids = @{}
    $programDataRoot = [IO.Path]::GetFullPath("$env:ProgramData\Poyi").TrimEnd('\') + '\'
    $programFilesRoot = [IO.Path]::GetFullPath("$env:ProgramFiles\Poyi").TrimEnd('\') + '\'
    foreach ($project in @($config.projects)) {
        $id = [string]$project.id
        if ($id -notmatch '^[a-z][a-z0-9_-]{0,31}$' -or $ids.ContainsKey($id)) {
            throw 'Invalid or duplicate fleet project id.'
        }
        $ids[$id] = $true
        $dataDir = [IO.Path]::GetFullPath([string]$project.dataDir)
        if (-not $dataDir.StartsWith(
                $programDataRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not ($project.PSObject.Properties.Name -contains 'grantModify')) {
            throw "Invalid data ACL configuration for $id."
        }
        foreach ($endpoint in @($project.mcp, $project.tunnel)) {
            if ([string]$endpoint.service -notmatch '^[A-Za-z0-9_-]{1,128}$') {
                throw "Invalid service name for $id."
            }
            $uri = $null
            if ($endpoint.PSObject.Properties.Name -contains 'health') {
                $uri = [Uri]([string]$endpoint.health)
            } elseif ($endpoint.PSObject.Properties.Name -contains 'ready') {
                $uri = [Uri]([string]$endpoint.ready)
            }
            if ($null -eq $uri -or $uri.Scheme -ne 'http' -or
                $uri.Host -notin @('127.0.0.1', 'localhost', '::1')) {
                throw "Non-loopback fleet endpoint for $id."
            }
        }
        if ($project.PSObject.Properties.Name -contains 'installDir') {
            $installDir = [IO.Path]::GetFullPath([string]$project.installDir)
            if (-not $installDir.StartsWith(
                    $programFilesRoot, [StringComparison]::OrdinalIgnoreCase) -or
                @($project.grantRead).Count -lt 1 -or
                @($project.verifyReadGlobs).Count -lt 1) {
                throw "Invalid runtime ACL configuration for $id."
            }
        }
    }
    return $config
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

function Test-PrincipalReadAndExecute([string]$Path, [string]$Principal) {
    try {
        $principalSid = ([Security.Principal.NTAccount]$Principal).Translate(
            [Security.Principal.SecurityIdentifier]).Value
        $required = [Security.AccessControl.FileSystemRights]::ReadAndExecute
        $allowed = $false
        $denied = $false
        foreach ($rule in (Get-Acl -LiteralPath $Path).Access) {
            try {
                $ruleSid = $rule.IdentityReference.Translate(
                    [Security.Principal.SecurityIdentifier]).Value
            } catch { continue }
            if ($ruleSid -ne $principalSid) { continue }
            $rights = $rule.FileSystemRights -band $required
            if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny) {
                if ($rights -ne 0) { $denied = $true }
            } elseif ($rights -eq $required) {
                $allowed = $true
            }
        }
        return ($allowed -and -not $denied)
    } catch {
        return $false
    }
}

function Repair-InstallDirAcls($Config, [string]$ServiceName = '') {
    $healthy = $true
    foreach ($project in $Config.projects) {
        if (-not [string]::IsNullOrWhiteSpace($ServiceName) -and
            $ServiceName -notin @([string]$project.mcp.service, [string]$project.tunnel.service)) {
            continue
        }
        if (-not ($project.PSObject.Properties.Name -contains 'installDir')) { continue }
        if (-not ($project.PSObject.Properties.Name -contains 'grantRead')) {
            Write-Log 'ERROR' "Missing grantRead for $($project.id)." | Out-Null
            $healthy = $false
            continue
        }
        $dir = [IO.Path]::GetFullPath([string]$project.installDir)
        $expectedRoot = [IO.Path]::GetFullPath("$env:ProgramFiles\Poyi").TrimEnd('\') + '\'
        if (-not $dir.StartsWith($expectedRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $dir -PathType Container)) {
            Write-Log 'ERROR' "Invalid or missing install dir for $($project.id)." | Out-Null
            $healthy = $false
            continue
        }
        foreach ($principal in @($project.grantRead)) {
            & icacls $dir /grant:r "$principal`:(OI)(CI)RX" /T /C /Q 2>$null | Out-Null
            $aclExitCode = $LASTEXITCODE
            if ($aclExitCode -ne 0) {
                Write-Log 'ERROR' "Runtime ACL grant failed for $($project.id)." | Out-Null
                $healthy = $false
            }
        }
        if (-not ($project.PSObject.Properties.Name -contains 'verifyReadGlobs')) {
            Write-Log 'ERROR' "Missing verifyReadGlobs for $($project.id)." | Out-Null
            $healthy = $false
            continue
        }
        $rootPrefix = $dir.TrimEnd('\') + '\'
        foreach ($pattern in @($project.verifyReadGlobs)) {
            $matches = @(Get-ChildItem -Path (Join-Path $dir ([string]$pattern)) -File `
                    -ErrorAction SilentlyContinue)
            if ($matches.Count -lt 1) {
                Write-Log 'ERROR' "Runtime ACL verification file missing for $($project.id)." |
                    Out-Null
                $healthy = $false
                continue
            }
            foreach ($file in $matches) {
                $resolved = [IO.Path]::GetFullPath($file.FullName)
                if (-not $resolved.StartsWith(
                        $rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                    Write-Log 'ERROR' "Runtime ACL verification escaped install root." | Out-Null
                    $healthy = $false
                    continue
                }
                foreach ($principal in @($project.grantRead)) {
                    if (-not (Test-PrincipalReadAndExecute $resolved ([string]$principal))) {
                        Write-Log 'ERROR' "Runtime ACL verification failed for $($project.id)." |
                            Out-Null
                        $healthy = $false
                    }
                }
            }
        }
    }
    if ($healthy) {
        Write-Log 'INFO' 'Runtime read ACL baseline applied and verified.' | Out-Null
    }
    return $healthy
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

# Per-service action bookkeeping: consecutive probe failures, restart budget, backoff.
$script:State = @{}

function Load-RestartState {
    try {
        if (-not (Test-Path -LiteralPath $script:RestartStatePath -PathType Leaf)) { return }
        if ((Get-Item -LiteralPath $script:RestartStatePath).Length -gt 65536) { return }
        $document = Get-Content -Raw -LiteralPath $script:RestartStatePath | ConvertFrom-Json
        if ($document.schemaVersion -ne 1 -or $null -eq $document.services) { return }
        $cutoff = (Get-Date).AddHours(-1)
        foreach ($property in $document.services.PSObject.Properties) {
            if ($property.Name -notmatch '^[A-Za-z0-9_-]{1,128}$') { continue }
            $attempts = @()
            foreach ($raw in @($property.Value)) {
                try {
                    $parsed = [DateTime]::Parse(
                        [string]$raw,
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::RoundtripKind)
                    if ($parsed -ge $cutoff -and $parsed -le (Get-Date).AddMinutes(1)) {
                        $attempts += $parsed
                    }
                } catch { }
            }
            $script:PersistedRestartAttempts[$property.Name] = @($attempts)
        }
    } catch {
        $script:PersistedRestartAttempts = @{}
    }
}

function Save-RestartState {
    $services = [ordered]@{}
    foreach ($name in $script:State.Keys) {
        $services[$name] = @($script:State[$name].Attempts | ForEach-Object {
                $_.ToUniversalTime().ToString('o')
            })
    }
    $document = [ordered]@{ schemaVersion = 1; services = $services }
    $json = $document | ConvertTo-Json -Depth 5
    $temporary = "$script:RestartStatePath.$PID.tmp"
    try {
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $script:RestartStatePath -Force
    } catch {
        Write-Log 'WARN' 'Could not persist restart budget state.' | Out-Null
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-TargetState([string]$Name) {
    if (-not $script:State.ContainsKey($Name)) {
        $attempts = @()
        if ($script:PersistedRestartAttempts.ContainsKey($Name)) {
            $attempts = @($script:PersistedRestartAttempts[$Name])
        }
        $lastRestart = [DateTime]::MinValue
        if ($attempts.Count -gt 0) {
            $lastRestart = @($attempts | Sort-Object -Descending)[0]
        }
        $script:State[$Name] = @{
            Fails = 0; LastRestart = $lastRestart
            Attempts = @($attempts); BackoffUntil = [DateTime]::MinValue
        }
    }
    $script:State[$Name]
}

function Test-RestartAllowed($TargetState, $Config) {
    $now = Get-Date
    if ($now -lt $TargetState.BackoffUntil) { return $false }
    if (($now - $TargetState.LastRestart).TotalSeconds -lt $Config.restartCooldownSeconds) { return $false }
    $cutoff = $now.AddHours(-1)
    $TargetState.Attempts = @($TargetState.Attempts | Where-Object { $_ -ge $cutoff })
    if ($TargetState.Attempts.Count -ge $Config.maxRestartsPerHour) {
        $TargetState.BackoffUntil = $now.AddSeconds($Config.backoffSeconds)
        Write-Log 'WARN' 'Hourly restart budget exhausted; backing off.'
        Write-FleetEvent 9004 'Warning' 'Hourly restart budget exhausted; backing off.'
        return $false
    }
    return $true
}

function Register-Restart($TargetState) {
    $now = Get-Date
    $TargetState.LastRestart = $now
    $TargetState.Attempts = @($TargetState.Attempts) + @($now)
    $TargetState.Fails = 0
    Save-RestartState
}

function Start-FleetService([string]$Name, $Config) {
    Repair-DataDirAcls $Config | Out-Null
    if (-not (Repair-InstallDirAcls $Config $Name)) {
        Write-Log 'ERROR' "Service $Name not started because runtime ACL verification failed."
        return $false
    }
    Write-Log 'INFO' "Starting service $Name."
    & sc.exe start $Name | Out-Null
    if (-not (Wait-ServiceStatus $Name 'Running' 30)) {
        Write-Log 'ERROR' "Service $Name failed to start."
        Write-FleetEvent 9005 'Error' "Service $Name failed to start."
        return $false
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
        Write-Log 'ERROR' "$mcpName did not stop cleanly; refusing to kill an unverified PID."
        return
    }
    Start-FleetService $mcpName $Config | Out-Null
    Start-Sleep -Seconds 3
    Start-FleetService $tunnelName $Config | Out-Null
}

function Restart-TunnelService($Project, $Config) {
    $tunnelName = $Project.tunnel.service
    Write-Log 'WARN' "Force-restarting $tunnelName (readiness probe kept failing)."
    Write-FleetEvent 9003 'Warning' "Force-restarting $tunnelName after repeated failed readiness probes."
    & sc.exe stop $tunnelName | Out-Null
    if (-not (Wait-ServiceStatus $tunnelName 'Stopped' 30)) {
        Write-Log 'ERROR' "$tunnelName did not stop cleanly; refusing to kill an unverified PID."
        return
    }
    Start-FleetService $tunnelName $Config | Out-Null
}

function Invoke-FleetPass($Config, [bool]$InBootGrace, [bool]$Forced = $false) {
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
                $due = $Forced -or ($mcpTarget.Fails -ge $Config.mcpFailThreshold)
                if (-not $InBootGrace -and $due -and
                    (Test-RestartAllowed $mcpTarget $Config)) {
                    Register-Restart $mcpTarget
                    Restart-McpService $project $Config
                    $mcpHealthy = Test-Probe $project.mcp.health $Config.probeTimeoutSeconds
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
            if (Test-RestartAllowed $tunnelTarget $Config) {
                Register-Restart $tunnelTarget
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
            $due = $Forced -or ($tunnelTarget.Fails -ge $Config.tunnelFailThreshold)
            if (-not $InBootGrace -and $due -and
                (Test-RestartAllowed $tunnelTarget $Config)) {
                Register-Restart $tunnelTarget
                Restart-TunnelService $project $Config
            }
        }
    }
}

function Test-RepairTrigger($Config) {
    $candidates = @(Get-ChildItem -LiteralPath $script:TriggerDir -Filter 'repair-*.json' `
            -File -ErrorAction SilentlyContinue)
    $requests = @()
    foreach ($request in $candidates) {
        if ($request.Name -notmatch '^repair-([A-Za-z0-9_-]{1,32})\.json$' -or
            $request.Length -gt 4096 -or
            ($request.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            continue
        }
        try {
            $payload = Get-Content -Raw -LiteralPath $request.FullName | ConvertFrom-Json
            $names = @($payload.PSObject.Properties.Name | Sort-Object)
            $requestId = [Guid]::Empty
            if (($names -join ',') -ne 'requestId,requestedAt,schemaVersion,source' -or
                $payload.schemaVersion -ne 1 -or
                [string]$payload.source -ne $Matches[1] -or
                -not [Guid]::TryParse([string]$payload.requestId, [ref]$requestId)) {
                continue
            }
            $requestedAt = [DateTime]::Parse(
                [string]$payload.requestedAt,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind)
            $age = ((Get-Date) - $requestedAt).TotalSeconds
            if ($age -lt -60 -or $age -gt 600) { continue }
            $requests += $request
        } catch { }
    }
    if ($requests.Count -eq 0) { return $false }
    if (Test-Path -LiteralPath $script:MaintenanceFlag) {
        Write-Log 'WARN' 'Repair request ignored: maintenance.flag is present.'
        Write-FleetEvent 9006 'Warning' 'Repair request ignored during maintenance.'
        return $false
    }
    if (((Get-Date) - $script:LastForcedPass).TotalSeconds -lt 60) {
        Write-Log 'WARN' 'Repair request rate-limited (one forced pass per minute).'
        return $false
    }
    $claimed = @()
    foreach ($request in $requests) {
        $claim = "$($request.FullName).processing"
        try {
            Move-Item -LiteralPath $request.FullName -Destination $claim -ErrorAction Stop
            $claimed += $claim
        } catch { }
    }
    if ($claimed.Count -eq 0) { return $false }
    $script:LastForcedPass = Get-Date
    Write-Log 'INFO' ("Repair requested ({0} trigger file(s)); running forced pass." -f $claimed.Count)
    Write-FleetEvent 9006 'Information' 'Repair requested via trigger; running forced remediation pass.'
    Repair-DataDirAcls $Config
    if (-not (Repair-InstallDirAcls $Config)) {
        foreach ($claim in $claimed) {
            Move-Item -LiteralPath $claim -Destination ($claim -replace '\.processing$', '') `
                -Force -ErrorAction SilentlyContinue
        }
        Write-FleetEvent 9007 'Error' 'Repair stopped because runtime ACL verification failed.'
        return $false
    }
    Invoke-FleetPass $Config $false $true
    foreach ($claim in $claimed) {
        Remove-Item -LiteralPath $claim -Force -ErrorAction SilentlyContinue
    }
    Write-Log 'INFO' 'Forced remediation pass finished.'
    return $true
}

function Invoke-CloudSync($Config) {
    if (-not ($Config.PSObject.Properties.Name -contains 'cloudSync')) { return }
    if (-not $Config.cloudSync.enabled) { return }
    if (-not (Test-Path -LiteralPath $Config.cloudSync.config)) {
        Write-Log 'WARN' 'cloud sync enabled but its config file is missing.'
        return
    }
    $pythonDirs = @(Get-ChildItem -LiteralPath "$env:ProgramFiles\Poyi\PersonalMcpGateway\python" `
        -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'cpython-3.12.*' })
    if ($pythonDirs.Count -lt 1) {
        Write-Log 'WARN' 'cloud sync skipped: private Python runtime not found.'
        return
    }
    $python = Join-Path $pythonDirs[0].FullName 'python.exe'
    $script = Join-Path $script:BaseDir 'cloud_sync.py'
    try {
        $arguments = @('-s', $script, '--config', [string]$Config.cloudSync.config)
        if ($Config.cloudSync.PSObject.Properties.Name -contains 'status') {
            $arguments += @('--status', [string]$Config.cloudSync.status)
        }
        $output = & $python @arguments 2>&1
        foreach ($line in @($output)) { Write-Log 'INFO' ("sync: {0}" -f $line) }
    } catch {
        Write-Log 'WARN' ("cloud sync failed: {0}" -f $_.Exception.Message)
    }
}

# --- main ---
$config = Get-Config
Write-Log 'INFO' ("Fleet watchdog starting; monitoring {0} projects." -f @($config.projects).Count)
Write-FleetEvent 9001 'Information' 'Fleet watchdog started.'
Load-RestartState
Repair-DataDirAcls $config
Repair-InstallDirAcls $config | Out-Null
$script:PassCount = 0

while ($true) {
    try {
        $triggered = Test-RepairTrigger $config
        if (Test-Path -LiteralPath $script:MaintenanceFlag) {
            Write-Log 'INFO' 'maintenance.flag present; skipping this pass.'
        } elseif (-not $triggered) {
            $inBootGrace = (Get-UptimeSeconds) -lt $config.bootGraceSeconds
            Invoke-FleetPass $config $inBootGrace
        }
        $script:PassCount = $script:PassCount + 1
        $syncEvery = 10
        if ($config.PSObject.Properties.Name -contains 'cloudSync' -and
            $config.cloudSync.PSObject.Properties.Name -contains 'everyPasses') {
            $syncEvery = [Math]::Max(1, [int]$config.cloudSync.everyPasses)
        }
        if ((($script:PassCount - 1) % $syncEvery) -eq 0) { Invoke-CloudSync $config }
    } catch {
        Write-Log 'ERROR' ("Watchdog pass failed: {0}" -f $_.Exception.Message)
    }
    # Poll triggers frequently so the board's repair button feels immediate,
    # while full probe passes keep their configured cadence.
    $slept = 0
    while ($slept -lt $config.loopSeconds) {
        Start-Sleep -Seconds 3
        $slept += 3
        if (@(Get-ChildItem -LiteralPath $script:TriggerDir -File -ErrorAction SilentlyContinue).Count -gt 0) {
            break
        }
    }
}
