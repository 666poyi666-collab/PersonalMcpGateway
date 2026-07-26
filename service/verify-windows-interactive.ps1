[CmdletBinding()]
param(
    [switch]$Elevated,
    [switch]$SkipRestartGates
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    if ($Elevated) { throw 'Administrator elevation was not granted.' }
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath,
        '-Elevated'
    )
    if ($SkipRestartGates) { $arguments += '-SkipRestartGates' }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
        -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$evidenceDir = Join-Path $repositoryRoot 'evidence'
$dataDir = "$env:ProgramData\Poyi\PersonalMcpGateway"
$installedRoot = "$env:ProgramFiles\Poyi\PersonalMcpGateway"
$resultPath = Join-Path $evidenceDir 'windows-verification-result.json'
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

try {
    if ($SkipRestartGates) {
        foreach ($name in @('gateway-restart-20.json', 'tunnel-restart-20.json')) {
            $gate = Get-Content -Raw -LiteralPath (Join-Path $evidenceDir $name) |
                ConvertFrom-Json
            if ($gate.status -ne 'passed' -or $gate.checks -ne 40) {
                throw "Existing restart evidence $name is incomplete."
            }
        }
    } else {
        & (Join-Path $repositoryRoot 'tests\soak\run-gates.ps1') -Gate gateway-restart-20 `
            -EvidenceDir $evidenceDir
        & (Join-Path $repositoryRoot 'tests\soak\run-gates.ps1') -Gate tunnel-restart-20 `
            -EvidenceDir $evidenceDir
    }
    $doctorEvidence = Join-Path $evidenceDir 'tunnel-doctor-redacted.txt'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `
        (Join-Path $installedRoot 'tunnel\doctor.ps1') -DataDir $dataDir `
        -OutputPath $doctorEvidence
    $doctorExitCode = $LASTEXITCODE
    $doctorResult = Get-Content -Raw -LiteralPath $doctorEvidence | ConvertFrom-Json
    $failedChecks = @($doctorResult.failed_checks)
    $expectedOAuthWarning = $doctorExitCode -ne 0 -and $failedChecks.Count -eq 1 -and `
        $failedChecks[0] -eq 'oauth_metadata'
    if ($doctorExitCode -ne 0 -and -not $expectedOAuthWarning) {
        throw "Tunnel doctor failed checks: $($failedChecks -join ', ')."
    }
    $doctorStatus = if ($expectedOAuthWarning) {
        'passed_with_expected_loopback_oauth_warning'
    } else {
        'passed'
    }

    $services = @(Get-CimInstance Win32_Service | Where-Object {
        $_.Name -in @('PoyiPersonalMcpGateway', 'OpenAISecureMcpTunnel')
    })
    if ($services.Count -ne 2) { throw 'Expected both Windows services to be installed.' }
    foreach ($service in $services) {
        if ($service.State -ne 'Running' -or $service.StartMode -ne 'Auto') {
            throw "Service $($service.Name) did not pass final state validation."
        }
    }
    $expectedAccounts = @{
        PoyiPersonalMcpGateway = 'NT SERVICE\PoyiPersonalMcpGateway'
        OpenAISecureMcpTunnel = 'NT SERVICE\OpenAISecureMcpTunnel'
    }
    foreach ($service in $services) {
        if ($service.StartName -ne $expectedAccounts[$service.Name]) {
            throw "Service $($service.Name) is using an unexpected account."
        }
    }
    [void](Invoke-RestMethod 'http://127.0.0.1:8761/healthz' -TimeoutSec 5)
    [void](Invoke-RestMethod 'http://127.0.0.1:8761/readyz' -TimeoutSec 5)
    [void](Invoke-RestMethod 'http://127.0.0.1:8877/healthz' -TimeoutSec 5)
    [void](Invoke-RestMethod 'http://127.0.0.1:8877/readyz' -TimeoutSec 5)
    [ordered]@{
        status = 'passed'
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        services = @($services | Sort-Object Name | ForEach-Object {
            [ordered]@{
                name = $_.Name
                state = $_.State
                startMode = $_.StartMode
                account = $_.StartName
            }
        })
        gatewayRestartCount = 20
        tunnelRestartCount = 20
        tunnelDoctor = $doctorStatus
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    [ordered]@{
        status = 'failed'
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        error = $_.Exception.Message
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
}
