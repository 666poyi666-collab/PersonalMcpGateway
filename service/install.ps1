[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\Poyi\PersonalMcpGateway",
    [string]$DataDir = "$env:ProgramData\Poyi\PersonalMcpGateway",
    [string]$TunnelId,
    [Security.SecureString]$RuntimeApiKey
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run install.ps1 from an elevated PowerShell session.'
    }
}

function Get-VerifiedDownload([string]$Url, [string]$Sha256, [string]$Destination) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $Destination -Force
        throw "SHA-256 mismatch for $Url"
    }
}

function Protect-Secret([Security.SecureString]$Secret, [string]$Destination) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
        $entropy = [Text.Encoding]::UTF8.GetBytes('Poyi.PersonalMcpGateway.v1')
        $encrypted = [Security.Cryptography.ProtectedData]::Protect(
            $bytes, $entropy, [Security.Cryptography.DataProtectionScope]::LocalMachine)
        [IO.File]::WriteAllText($Destination, [Convert]::ToBase64String($encrypted))
        [Array]::Clear($bytes, 0, $bytes.Length)
    } finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Set-ServiceDataDir(
    [string]$ConfigurationPath,
    [string]$ResolvedDataDir,
    [string]$ServiceLogName
) {
    [xml]$configuration = Get-Content -Raw -LiteralPath $ConfigurationPath
    foreach ($node in @($configuration.SelectNodes('/service/env'))) {
        if ($node.GetAttribute('name') -eq 'PERSONAL_MCP_DATA_DIR') {
            $node.SetAttribute('value', $ResolvedDataDir)
        }
    }
    $logPathNode = $configuration.SelectSingleNode('/service/logpath')
    if ($null -eq $logPathNode) { throw "Missing logpath in $ConfigurationPath" }
    $logPathNode.InnerText = Join-Path $ResolvedDataDir "service-logs\$ServiceLogName"
    $configuration.Save($ConfigurationPath)

    [xml]$saved = Get-Content -Raw -LiteralPath $ConfigurationPath
    $dataNode = @($saved.SelectNodes('/service/env')) | Where-Object {
        $_.GetAttribute('name') -eq 'PERSONAL_MCP_DATA_DIR'
    } | Select-Object -First 1
    if ($null -eq $dataNode -or $dataNode.GetAttribute('value') -ne $ResolvedDataDir) {
        throw "Failed to set PERSONAL_MCP_DATA_DIR in $ConfigurationPath"
    }
}

function Set-GatewayRuntime([string]$ConfigurationPath, [string]$PythonExecutable) {
    [xml]$configuration = Get-Content -Raw -LiteralPath $ConfigurationPath
    $executableNode = $configuration.SelectSingleNode('/service/executable')
    $argumentsNode = $configuration.SelectSingleNode('/service/arguments')
    if ($null -eq $executableNode -or $null -eq $argumentsNode) {
        throw "Missing executable or arguments in $ConfigurationPath"
    }
    $executableNode.InnerText = $PythonExecutable
    $argumentsNode.InnerText = '-s -m personal_mcp_gateway.service_bootstrap serve'
    $configuration.Save($ConfigurationPath)

    [xml]$saved = Get-Content -Raw -LiteralPath $ConfigurationPath
    if ($saved.service.executable -ne $PythonExecutable -or
        $saved.service.arguments -ne '-s -m personal_mcp_gateway.service_bootstrap serve') {
        throw "Failed to set the private Python runtime in $ConfigurationPath"
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

function Stop-InstalledListenerProcesses([string]$ResolvedInstallDir) {
    $installPrefix = [IO.Path]::GetFullPath($ResolvedInstallDir).TrimEnd('\') + '\'
    $processIds = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @(8760, 8761, 8877) } |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($processId in $processIds) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" `
            -ErrorAction SilentlyContinue
        if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.ExecutablePath)) {
            continue
        }
        $executable = [IO.Path]::GetFullPath($process.ExecutablePath)
        if ($executable.StartsWith($installPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
    }
}

function Wait-ServiceRunning([string]$Name, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -ne $service -and $service.Status -eq 'Running') { return }
        Start-Sleep -Milliseconds 500
    } until ((Get-Date) -ge $deadline)
    throw "Service $Name did not remain running within $TimeoutSeconds seconds."
}

function Wait-ReadyEndpoint([string]$Uri, [string]$Name, [int]$TimeoutSeconds = 45) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try { $response = Invoke-RestMethod $Uri -TimeoutSec 2 }
        catch { $response = $null }
        if ($null -ne $response) { return }
        Start-Sleep -Milliseconds 500
    } until ((Get-Date) -ge $deadline)
    throw "$Name did not become ready within $TimeoutSeconds seconds."
}

Assert-Administrator
Add-Type -AssemblyName System.Security
$sourceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$resolvedSource = (Resolve-Path -LiteralPath $sourceRoot).Path
$resolvedInstallParent = [IO.Path]::GetFullPath((Split-Path -Parent $InstallDir))
$resolvedDataParent = [IO.Path]::GetFullPath((Split-Path -Parent $DataDir))
if ($resolvedInstallParent -notlike "$env:ProgramFiles*") { throw 'InstallDir must be under Program Files.' }
if ($resolvedDataParent -notlike "$env:ProgramData*") { throw 'DataDir must be under ProgramData.' }

New-Item -ItemType Directory -Path $InstallDir, $DataDir -Force | Out-Null
Get-ChildItem -LiteralPath $resolvedSource -Force | Where-Object {
    $_.Name -notin @('.git', '.venv', '.pytest_cache', 'dist', 'build')
} | Copy-Item -Destination $InstallDir -Recurse -Force

# WatchIntervals now owns an independent MCP service. Remove files left by older upgrades.
Remove-Item -LiteralPath (Join-Path $InstallDir 'modules\watch.yaml') `
    -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $DataDir 'watch-token.dpapi') `
    -Force -ErrorAction SilentlyContinue

$gatewayExe = Join-Path $InstallDir 'PoyiPersonalMcpGateway.exe'
$tunnelExe = Join-Path $InstallDir 'OpenAISecureMcpTunnel.exe'
foreach ($service in @(
    @{ Name = 'OpenAISecureMcpTunnel'; Executable = $tunnelExe },
    @{ Name = 'PoyiPersonalMcpGateway'; Executable = $gatewayExe }
)) {
    $installed = Get-Service -Name $service.Name -ErrorAction SilentlyContinue
    if ($null -ne $installed) {
        if ($installed.Status -ne 'Stopped') {
            Stop-Service -Name $service.Name -Force
            (Get-Service -Name $service.Name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        }
        & $service.Executable uninstall
        if ($LASTEXITCODE -ne 0) { throw "Failed to uninstall service $($service.Name)." }
    }
}
Stop-InstalledListenerProcesses $InstallDir

$dependencies = Get-Content -Raw -LiteralPath (Join-Path $InstallDir 'service\dependencies.json') |
    ConvertFrom-Json
$downloadDir = Join-Path $env:TEMP ('personal-mcp-install-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadDir | Out-Null
try {
    $winsw = Join-Path $downloadDir 'WinSW-x64.exe'
    Get-VerifiedDownload $dependencies.winsw.url $dependencies.winsw.sha256 $winsw
    Copy-Item $winsw (Join-Path $InstallDir 'PoyiPersonalMcpGateway.exe') -Force
    Copy-Item $winsw (Join-Path $InstallDir 'OpenAISecureMcpTunnel.exe') -Force
    Copy-Item (Join-Path $InstallDir 'service\gateway-service.xml') `
        (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') -Force
    Copy-Item (Join-Path $InstallDir 'service\tunnel-service.xml') `
        (Join-Path $InstallDir 'OpenAISecureMcpTunnel.xml') -Force
    Set-ServiceDataDir (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') `
        ([IO.Path]::GetFullPath($DataDir)) 'gateway'
    Set-ServiceDataDir (Join-Path $InstallDir 'OpenAISecureMcpTunnel.xml') `
        ([IO.Path]::GetFullPath($DataDir)) 'tunnel'

    $tunnelZip = Join-Path $downloadDir 'tunnel-client.zip'
    Get-VerifiedDownload $dependencies.tunnelClient.url $dependencies.tunnelClient.sha256 $tunnelZip
    Expand-Archive -LiteralPath $tunnelZip -DestinationPath (Join-Path $InstallDir 'tunnel-client') -Force
} finally {
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue
}

Push-Location $InstallDir
try {
    $pythonInstallDir = Join-Path $InstallDir 'python'
    $previousPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
    $previousNoUserSite = $env:PYTHONNOUSERSITE
    $env:UV_PYTHON_INSTALL_DIR = $pythonInstallDir
    $env:PYTHONNOUSERSITE = '1'
    & uv python install 3.12 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw 'uv python install failed.' }
    $pythonRuntimes = @(Get-ChildItem -LiteralPath $pythonInstallDir -Directory |
        Where-Object { $_.Name -like 'cpython-3.12.*-windows-x86_64-none' } |
        Sort-Object Name -Descending)
    if ($pythonRuntimes.Count -lt 1) {
        throw 'Could not resolve the installed Python 3.12 runtime.'
    }
    $pythonExe = Join-Path $pythonRuntimes[0].FullName 'python.exe'
    $resolvedPython = [IO.Path]::GetFullPath($pythonExe)
    $resolvedPythonRoot = [IO.Path]::GetFullPath($pythonInstallDir) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPython.StartsWith($resolvedPythonRoot,
            [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $resolvedPython)) {
        throw 'Resolved Python is outside the private runtime directory.'
    }
    $pythonExe = $resolvedPython
    $runtimeBuildDir = Join-Path $env:TEMP `
        ('personal-mcp-runtime-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $runtimeBuildDir | Out-Null
    try {
        $requirementsPath = Join-Path $runtimeBuildDir 'requirements.txt'
        & uv export --locked --no-dev --no-emit-project --format requirements.txt `
            --output-file $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw 'uv export failed.' }
        & uv build --wheel --out-dir $runtimeBuildDir
        if ($LASTEXITCODE -ne 0) { throw 'uv build failed.' }
        $wheels = @(Get-ChildItem -LiteralPath $runtimeBuildDir -Filter '*.whl' -File)
        if ($wheels.Count -ne 1) { throw 'Expected exactly one gateway wheel.' }
        & uv pip install --python $pythonExe --break-system-packages `
            --requirement $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw 'Locked runtime dependency installation failed.' }
        & uv pip install --python $pythonExe --break-system-packages --no-deps `
            --reinstall $wheels[0].FullName
        if ($LASTEXITCODE -ne 0) { throw 'Gateway wheel installation failed.' }
    } finally {
        Remove-Item -LiteralPath $runtimeBuildDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $pythonExe -c 'import mcp, personal_mcp_gateway, pywintypes, uvicorn, win32crypt'
    if ($LASTEXITCODE -ne 0) { throw 'Private Python runtime import verification failed.' }
    Set-GatewayRuntime (Join-Path $InstallDir 'PoyiPersonalMcpGateway.xml') $pythonExe
} finally {
    $env:UV_PYTHON_INSTALL_DIR = $previousPythonInstallDir
    $env:PYTHONNOUSERSITE = $previousNoUserSite
    Pop-Location
}

if (-not [string]::IsNullOrWhiteSpace($TunnelId)) {
    if ($TunnelId -notmatch '^tunnel_[A-Za-z0-9_-]+$') { throw 'Invalid TunnelId.' }
    if ($null -eq $RuntimeApiKey) {
        $RuntimeApiKey = Read-Host 'OpenAI Tunnel Runtime API Key' -AsSecureString
    }
    Protect-Secret $RuntimeApiKey (Join-Path $DataDir 'runtime-key.dpapi')
    Set-Content -LiteralPath (Join-Path $DataDir 'tunnel-id') -Value $TunnelId -Encoding ASCII
}

& $gatewayExe install
if ($LASTEXITCODE -ne 0) { throw 'Gateway service installation failed.' }
& $tunnelExe install
if ($LASTEXITCODE -ne 0) { throw 'Tunnel service installation failed.' }

$gatewaySid = 'NT SERVICE\PoyiPersonalMcpGateway'
$tunnelSid = 'NT SERVICE\OpenAISecureMcpTunnel'
& icacls $InstallDir /grant:r "$gatewaySid`:(OI)(CI)RX" "$tunnelSid`:(OI)(CI)RX" `
    /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to grant service read access to the install directory.' }
Assert-ServiceRuntimeReadAccess $InstallDir $gatewaySid
$gatewayLogDir = Join-Path $DataDir 'logs'
$serviceLogDir = Join-Path $DataDir 'service-logs'
$gatewayServiceLogDir = Join-Path $serviceLogDir 'gateway'
$tunnelServiceLogDir = Join-Path $serviceLogDir 'tunnel'
$tunnelLogDir = Join-Path $DataDir 'tunnel-logs'
New-Item -ItemType Directory -Path $gatewayLogDir, $gatewayServiceLogDir, `
    $tunnelServiceLogDir, $tunnelLogDir -Force | Out-Null
& icacls $DataDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" "$tunnelSid`:(RX)" | Out-Null
& icacls $gatewayLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to configure Gateway log directory ACLs.' }
& icacls $gatewayLogDir /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$gatewaySid`:(OI)(CI)M" /T /C | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Some historical Gateway log ACLs could not be updated.'
}
$gatewayLogPath = Join-Path $gatewayLogDir 'gateway.jsonl'
if (Test-Path -LiteralPath $gatewayLogPath) {
    & icacls $gatewayLogPath /inheritance:r `
        /grant:r 'BUILTIN\Administrators:F' "$gatewaySid`:M" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'The historical Gateway log remains stderr-only.'
    }
}
& icacls $serviceLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' |
    Out-Null
& icacls $gatewayServiceLogDir /inheritance:r `
    /grant:r 'BUILTIN\Administrators:(OI)(CI)F' "$gatewaySid`:(OI)(CI)M" /T |
    Out-Null
& icacls $tunnelServiceLogDir /inheritance:r `
    /grant:r 'BUILTIN\Administrators:(OI)(CI)F' "$tunnelSid`:(OI)(CI)M" /T |
    Out-Null
& icacls $tunnelLogDir /inheritance:r /grant:r 'BUILTIN\Administrators:(OI)(CI)F' `
    "$tunnelSid`:(OI)(CI)M" /T | Out-Null
foreach ($name in @('gateway.db', 'gateway.db-wal', 'gateway.db-shm',
        'admin-token', 'admin-csrf-token')) {
    $path = Join-Path $DataDir $name
    if (Test-Path -LiteralPath $path) {
        & icacls $path /inheritance:r /grant:r 'BUILTIN\Administrators:F' `
            "$gatewaySid`:M" | Out-Null
    }
}
& icacls (Join-Path $DataDir 'runtime-key.dpapi') /inheritance:r `
    /grant:r 'BUILTIN\Administrators:F' "$tunnelSid`:R" 2>$null | Out-Null
& icacls (Join-Path $DataDir 'tunnel-id') /inheritance:r `
    /grant:r 'BUILTIN\Administrators:F' "$tunnelSid`:R" 2>$null | Out-Null

& $gatewayExe start
Wait-ServiceRunning 'PoyiPersonalMcpGateway'
Wait-ReadyEndpoint 'http://127.0.0.1:8761/healthz' 'Gateway health endpoint'
Wait-ReadyEndpoint 'http://127.0.0.1:8761/readyz' 'Gateway readiness endpoint'
if (Test-Path -LiteralPath (Join-Path $DataDir 'runtime-key.dpapi')) {
    & $tunnelExe start
    Wait-ServiceRunning 'OpenAISecureMcpTunnel'
    Wait-ReadyEndpoint 'http://127.0.0.1:8877/readyz' 'Tunnel readiness endpoint'
    Start-Sleep -Seconds 3
    Wait-ServiceRunning 'OpenAISecureMcpTunnel' 5
}

Write-Host "Installed Personal MCP Gateway in $InstallDir" -ForegroundColor Green
