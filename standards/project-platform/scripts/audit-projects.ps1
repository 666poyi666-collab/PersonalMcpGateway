[CmdletBinding()]
param(
    [string]$RegistryPath = '',
    [switch]$Live,
    [switch]$Strict,
    [Alias('RequireManifests')]
    [switch]$ManifestStrict,
    [switch]$VerifyEvidence,
    [string]$ReportPath = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RegistryPath) {
    $RegistryPath = Join-Path $PSScriptRoot '..\projects.json'
}
if (-not $ReportPath) {
    $ReportPath = Join-Path $PSScriptRoot '..\reports\audit-latest.md'
}

function Normalize-PathValue([string]$PathValue) {
    if (-not $PathValue) { return '' }
    return [System.IO.Path]::GetFullPath($PathValue).TrimEnd('\').ToLowerInvariant()
}

function Normalize-RepositoryUrl([string]$Url) {
    if (-not $Url) { return '' }
    return $Url.Trim().TrimEnd('/').ToLowerInvariant() -replace '\.git$', ''
}

function Test-HasProperty([object]$Value, [string]$Name) {
    return $null -ne $Value -and $null -ne $Value.PSObject.Properties[$Name]
}

function Test-StringSetEqual([object[]]$Left, [object[]]$Right) {
    $leftValues = @($Left | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $rightValues = @($Right | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    return $null -eq (Compare-Object -ReferenceObject $leftValues -DifferenceObject $rightValues)
}

function Compare-SyncCursor([string]$Left, [string]$Right) {
    if ($Left -notmatch '^c[0-9a-z]+$' -or $Right -notmatch '^c[0-9a-z]+$') {
        return $null
    }
    $leftDigits = $Left.Substring(1).TrimStart('0')
    $rightDigits = $Right.Substring(1).TrimStart('0')
    if (-not $leftDigits) { $leftDigits = '0' }
    if (-not $rightDigits) { $rightDigits = '0' }
    if ($leftDigits.Length -lt $rightDigits.Length) { return -1 }
    if ($leftDigits.Length -gt $rightDigits.Length) { return 1 }
    return [System.StringComparer]::Ordinal.Compare($leftDigits, $rightDigits)
}

function Test-PlausibleSha256([string]$Value) {
    if ($Value -notmatch '^[0-9a-fA-F]{64}$') { return $false }
    return @($Value.ToLowerInvariant().ToCharArray() | Sort-Object -Unique).Count -ge 8
}

function Test-HttpEndpoint([string]$Url) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 0
        $timer.Stop()
        return [pscustomobject]@{
            Url = $Url
            Ok = $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
            Status = [int]$response.StatusCode
            Milliseconds = $timer.ElapsedMilliseconds
            Error = ''
        }
    } catch {
        $timer.Stop()
        return [pscustomobject]@{
            Url = $Url
            Ok = $false
            Status = 0
            Milliseconds = $timer.ElapsedMilliseconds
            Error = $_.Exception.GetType().Name
        }
    }
}

function Get-HttpStatus([string]$Url, [string]$Method = 'GET') {
    try {
        $response = Invoke-WebRequest -Uri $Url -Method $Method -UseBasicParsing -TimeoutSec 15 `
            -MaximumRedirection 0
        return [int]$response.StatusCode
    } catch {
        if ($null -ne $_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

function Get-Sha256Text([string]$Value) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $bytes = $encoding.GetBytes($Value)
        return ([System.BitConverter]::ToString(
                $algorithm.ComputeHash($bytes)
            )).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Get-DirectorySourceTreeHash([string]$Root, [object[]]$IncludePaths) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return [pscustomobject]@{ Ok = $false; Hash = ''; Error = 'source root is missing' }
    }
    if (@($IncludePaths).Count -eq 0) {
        return [pscustomobject]@{ Ok = $false; Hash = ''; Error = 'includePaths is empty' }
    }

    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $files = New-Object 'System.Collections.Generic.Dictionary[string,System.IO.FileInfo]' (
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $blockedDirectories = @(
        '.git', '.wrangler', '.venv', 'node_modules', 'dist', 'build', 'coverage',
        '__pycache__', '.pytest_cache'
    )
    foreach ($include in @($IncludePaths)) {
        $relative = [string]$include
        if ([string]::IsNullOrWhiteSpace($relative) -or
            [System.IO.Path]::IsPathRooted($relative)) {
            return [pscustomobject]@{
                Ok = $false; Hash = ''; Error = "unsafe includePath: $relative"
            }
        }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot $relative))
        if (-not $candidate.StartsWith(
                $resolvedRoot + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            return [pscustomobject]@{
                Ok = $false; Hash = ''; Error = "includePath escapes source root: $relative"
            }
        }
        if (-not (Test-Path -LiteralPath $candidate)) {
            return [pscustomobject]@{
                Ok = $false; Hash = ''; Error = "includePath is missing: $relative"
            }
        }
        $reparsePoints = if (Test-Path -LiteralPath $candidate -PathType Container) {
            @(Get-ChildItem -LiteralPath $candidate -Recurse -Force | Where-Object {
                    $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint
                })
        } else {
            @(
                Get-Item -LiteralPath $candidate -Force | Where-Object {
                    $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint
                }
            )
        }
        if ($reparsePoints.Count -gt 0) {
            return [pscustomobject]@{
                Ok = $false; Hash = ''; Error = "includePath contains a reparse point: $relative"
            }
        }
        $candidates = if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            @(Get-Item -LiteralPath $candidate -Force)
        } else {
            @(Get-ChildItem -LiteralPath $candidate -Recurse -Force -File)
        }
        foreach ($file in $candidates) {
            $fileRelative = $file.FullName.Substring($resolvedRoot.Length).TrimStart('\', '/')
            $segments = @($fileRelative -split '[\\/]')
            if (@($segments | Where-Object { $_ -in $blockedDirectories }).Count -gt 0) {
                continue
            }
            $leaf = [string]$segments[-1]
            if ($leaf -eq '.dev.vars' -or $leaf -eq 'auth.json' -or
                $leaf -match '^\.env(?:\.|$)' -or
                $leaf -match '\.(?:pem|key|p12|pfx)$') {
                continue
            }
            $files[$file.FullName] = $file
        }
    }
    if ($files.Count -eq 0) {
        return [pscustomobject]@{ Ok = $false; Hash = ''; Error = 'source tree is empty' }
    }

    $records = foreach ($file in @($files.Values | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($resolvedRoot.Length).TrimStart('\', '/')
        $normalized = $relative.Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$normalized`0$($file.Length)`0$hash"
    }
    return [pscustomobject]@{
        Ok = $true
        Hash = Get-Sha256Text (($records -join "`n") + "`n")
        Error = ''
    }
}

function Escape-Markdown([object]$Value) {
    if ($null -eq $Value) { return '' }
    return ([string]$Value).Replace('|', '\|').Replace("`r", ' ').Replace("`n", ' ')
}

function Get-ManifestValidatorPython {
    $repositoryPython = [System.IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot '..\..\..\.venv\Scripts\python.exe')
    )
    if (Test-Path -LiteralPath $repositoryPython -PathType Leaf) {
        return $repositoryPython
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    return ''
}

function Add-ManifestIssue(
    [System.Collections.Generic.List[string]]$IssueList,
    [string]$ProjectId,
    [string]$Message
) {
    $IssueList.Add("$ProjectId manifest: $Message")
}

function Test-ManifestRoutes(
    [object]$Manifest,
    [hashtable]$ExpectedRoutes,
    [string]$ProjectId,
    [System.Collections.Generic.List[string]]$IssueList
) {
    $mcpRoutes = $Manifest.mcp.routes
    foreach ($name in @('mcp', 'health', 'ready', 'oauthResourceMetadata')) {
        if (-not (Test-HasProperty $mcpRoutes $name) -or
            $mcpRoutes.$name -ne $ExpectedRoutes[$name]) {
            Add-ManifestIssue $IssueList $ProjectId (
                "mcp.routes.$name must be $($ExpectedRoutes[$name])"
            )
        }
    }

    $syncRoutes = $Manifest.sync.routes
    if (-not (Test-HasProperty $syncRoutes 'exchange') -or
        $syncRoutes.exchange -ne $ExpectedRoutes.syncExchange) {
        Add-ManifestIssue $IssueList $ProjectId (
            "sync.routes.exchange must be $($ExpectedRoutes.syncExchange)"
        )
    }
    if (-not (Test-HasProperty $syncRoutes 'status') -or
        $syncRoutes.status -ne $ExpectedRoutes.syncStatus) {
        Add-ManifestIssue $IssueList $ProjectId (
            "sync.routes.status must be $($ExpectedRoutes.syncStatus)"
        )
    }
    if ($ProjectId -eq 'focuslink') {
        foreach ($name in @('pairOffers', 'pairExchange')) {
            if (-not (Test-HasProperty $syncRoutes $name) -or
                $syncRoutes.$name -ne $ExpectedRoutes[$name]) {
                Add-ManifestIssue $IssueList $ProjectId (
                    "sync.routes.$name must be $($ExpectedRoutes[$name])"
                )
            }
        }
    }
}

function Test-ManifestInventory(
    [object]$Manifest,
    [string]$ProjectId,
    [System.Collections.Generic.List[string]]$IssueList
) {
    $inventory = @($Manifest.dataInventory)
    if ($inventory.Count -eq 0) {
        Add-ManifestIssue $IssueList $ProjectId 'dataInventory must contain at least one item'
        return
    }

    $ids = @($inventory | ForEach-Object { $_.id })
    foreach ($duplicate in @($ids | Group-Object | Where-Object Count -gt 1)) {
        Add-ManifestIssue $IssueList $ProjectId "duplicate dataInventory id: $($duplicate.Name)"
    }

    foreach ($item in $inventory) {
        foreach ($field in @('id', 'classification', 'dataPlane', 'mcpExposure', 'coverage', 'reason')) {
            if (-not (Test-HasProperty $item $field)) {
                Add-ManifestIssue $IssueList $ProjectId "dataInventory item is missing $field"
            }
        }
        if ($item.dataPlane -notin @(
                'cloud_primary', 'derived_projection', 'snapshot_mirror', 'local_only'
            )) {
            Add-ManifestIssue $IssueList $ProjectId (
                "dataInventory.$($item.id) has invalid dataPlane: $($item.dataPlane)"
            )
        }
        if ($item.mcpExposure -notin @('tool', 'resource', 'both', 'none')) {
            Add-ManifestIssue $IssueList $ProjectId (
                "dataInventory.$($item.id) has invalid mcpExposure: $($item.mcpExposure)"
            )
        }
        if ($item.coverage -notin @('complete', 'partial', 'missing', 'exempt')) {
            Add-ManifestIssue $IssueList $ProjectId (
                "dataInventory.$($item.id) has invalid coverage: $($item.coverage)"
            )
        }
        if ([string]::IsNullOrWhiteSpace([string]$item.reason)) {
            Add-ManifestIssue $IssueList $ProjectId (
                "dataInventory.$($item.id) must include a non-empty reason"
            )
        }
    }
}

function Test-LocalOnlyManifest(
    [object]$Manifest,
    [string]$ProjectId,
    [System.Collections.Generic.List[string]]$IssueList
) {
    if ($Manifest.mcp.status -ne 'exempt') {
        Add-ManifestIssue $IssueList $ProjectId 'local_only requires mcp.status=exempt'
    }
    if ($Manifest.sync.status -ne 'exempt') {
        Add-ManifestIssue $IssueList $ProjectId 'local_only requires sync.status=exempt'
    }
    if ($Manifest.sync.authority -ne 'none') {
        Add-ManifestIssue $IssueList $ProjectId 'local_only requires sync.authority=none'
    }
    if ($Manifest.sync.supportsPcOff -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId 'local_only requires supportsPcOff=false'
    }
    if ($Manifest.sync.supportsBidirectionalDelta -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId (
            'local_only requires supportsBidirectionalDelta=false'
        )
    }
    foreach ($item in @($Manifest.dataInventory)) {
        if ($item.dataPlane -ne 'local_only') {
            Add-ManifestIssue $IssueList $ProjectId (
                "local_only inventory $($item.id) must use dataPlane=local_only"
            )
        }
        if ($item.mcpExposure -ne 'none') {
            Add-ManifestIssue $IssueList $ProjectId (
                "local_only inventory $($item.id) must use mcpExposure=none"
            )
        }
        if ($item.coverage -ne 'exempt') {
            Add-ManifestIssue $IssueList $ProjectId (
                "local_only inventory $($item.id) must use coverage=exempt"
            )
        }
    }
}

function Test-GatewayManifest(
    [object]$Manifest,
    [object]$Project,
    [string]$ProjectId,
    [System.Collections.Generic.List[string]]$IssueList
) {
    $diagnostics = $Manifest.runtimeDiagnostics
    if ($null -eq $diagnostics) {
        Add-ManifestIssue $IssueList $ProjectId 'runtimeDiagnostics declaration is required'
        return
    }
    if ($diagnostics.dataPlane -ne 'local_only') {
        Add-ManifestIssue $IssueList $ProjectId 'runtimeDiagnostics.dataPlane must be local_only'
    }
    if ($diagnostics.createsStateWhilePcOff -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId (
            'runtimeDiagnostics.createsStateWhilePcOff must be false'
        )
    }
    $allowedRecords = @($diagnostics.allowedCloudRecords)
    if (@($allowedRecords | Where-Object { $_ -notin @('last-heartbeat', 'audit') }).Count -gt 0) {
        Add-ManifestIssue $IssueList $ProjectId (
            'runtimeDiagnostics.allowedCloudRecords may contain only last-heartbeat and audit'
        )
    }
    if (-not (Test-StringSetEqual $allowedRecords @($Project.runtimeDiagnostics.allowedCloudRecords))) {
        Add-ManifestIssue $IssueList $ProjectId (
            'runtimeDiagnostics.allowedCloudRecords does not match the registry'
        )
    }
    $runtimeItem = @($Manifest.dataInventory | Where-Object id -eq 'runtime-diagnostics')
    if ($runtimeItem.Count -ne 1 -or $runtimeItem[0].dataPlane -ne 'local_only') {
        Add-ManifestIssue $IssueList $ProjectId (
            'runtime-diagnostics inventory must exist exactly once with dataPlane=local_only'
        )
    }
    if ($null -ne $Manifest.mcp.cloudBaseUrl) {
        Add-ManifestIssue $IssueList $ProjectId 'runtime diagnostics cannot register a cloudBaseUrl'
    }
    $profileItem = @($Manifest.dataInventory | Where-Object id -eq 'dashboard-profile')
    if ($profileItem.Count -ne 1 -or $profileItem[0].dataPlane -ne 'cloud_primary' -or
        $profileItem[0].mcpExposure -ne 'none' -or
        $profileItem[0].coverage -ne 'partial') {
        Add-ManifestIssue $IssueList $ProjectId (
            'dashboard-profile inventory must be partial cloud_primary with no MCP exposure'
        )
    }
    if ($Manifest.sync.status -ne 'partial' -or $Manifest.sync.authority -ne 'cloud' -or
        $Manifest.sync.mode -ne 'encrypted_dashboard_profile_local_staged_remote_unverified' -or
        $Manifest.sync.localImplementationStatus -ne (
            'browser_encrypted_outbox_exchange_boundary_implemented'
        ) -or
        $Manifest.sync.remoteVerificationStatus -ne 'missing' -or
        $Manifest.sync.supportsPcOff -ne $false -or
        $Manifest.sync.supportsBidirectionalDelta -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId (
            'Gateway dashboard profile sync must stay local-staged partial/cloud with ' +
            'remote verification missing and both PC-off capabilities false'
        )
    }
    $profileScript = Join-Path $Project.manifestRepositoryPath (
        'src\personal_mcp_gateway\admin\static\dashboard-profile.js'
    )
    if (-not (Test-Path -LiteralPath $profileScript -PathType Leaf)) {
        Add-ManifestIssue $IssueList $ProjectId 'dashboard profile implementation is missing'
    } else {
        $profileSource = Get-Content -LiteralPath $profileScript -Raw -Encoding UTF8
        foreach ($required in @(
                'name: "AES-GCM"',
                'database.transaction(["entities", "outbox", "meta"], "readwrite")',
                'prepareExchange',
                'applyExchange',
                'objectStore("conflicts")'
            )) {
            if (-not $profileSource.Contains($required)) {
                Add-ManifestIssue $IssueList $ProjectId (
                    "dashboard profile partial implementation is missing: $required"
                )
            }
        }
        if ($profileSource.Contains('fetch(')) {
            Add-ManifestIssue $IssueList $ProjectId (
                'dashboard profile must not claim a configured remote transport'
            )
        }
    }
}

function Test-FocusLinkManifest(
    [object]$Manifest,
    [string]$ManifestRaw,
    [object]$Project,
    [string]$ProjectId,
    [System.Collections.Generic.List[string]]$IssueList
) {
    $origin = 'https://foxlink-mcp.focuslink-poyi-6465e9.workers.dev'
    if ($Manifest.mcp.cloudBaseUrl -ne $origin -or $Manifest.sync.cloudBaseUrl -ne $origin) {
        Add-ManifestIssue $IssueList $ProjectId (
            'mcp.cloudBaseUrl and sync.cloudBaseUrl must both be the single foxlink public origin'
        )
    }
    $cloudBaseMatches = [regex]::Matches(
        $ManifestRaw,
        '"cloudBaseUrl"\s*:\s*"([^"]+)"'
    )
    $cloudOrigins = @($cloudBaseMatches | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    if ($cloudOrigins.Count -ne 1 -or $cloudOrigins[0] -ne $origin) {
        Add-ManifestIssue $IssueList $ProjectId 'manifest registers more than one public cloud origin'
    }
    if ($ManifestRaw -match 'focuslink-sync\.' -or $ManifestRaw -match '"/v2/sync') {
        Add-ManifestIssue $IssueList $ProjectId (
            'internal DO hostname and /v2/sync must not be registered in the manifest'
        )
    }
    if ($Manifest.sync.authority -ne 'cloud') {
        Add-ManifestIssue $IssueList $ProjectId 'sync.authority must be cloud'
    }
    if ($Manifest.mcp.dataPlane -ne 'derived_projection') {
        Add-ManifestIssue $IssueList $ProjectId 'mcp.dataPlane must be derived_projection'
    }
    if ($Manifest.mcp.projection.mode -ne 'sync_on_read' -or
        -not (Test-StringSetEqual @($Manifest.mcp.projection.requiredDiagnostics) @(
                'epoch', 'lag', 'degraded'
            ))) {
        Add-ManifestIssue $IssueList $ProjectId (
            'derived projection must be sync_on_read and expose epoch, lag, and degraded'
        )
    }
    if ($Manifest.credentialBoundary.mcp -ne 'oauth' -or
        $Manifest.credentialBoundary.sync -ne 'device_token' -or
        $Manifest.credentialBoundary.shared -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId (
            'MCP OAuth and device exchange credentials must be declared separate'
        )
    }
    if (@($Manifest.mcp.requiredScopes | Where-Object {
                $_ -in @('focuslink:pair', 'devices:manage')
            }).Count -gt 0) {
        Add-ManifestIssue $IssueList $ProjectId (
            'pairing permissions must never be registered as OAuth scopes'
        )
    }
    $pairing = $Manifest.sync.pairing
    if ($pairing.status -ne 'closed_pending_as_binding_and_joint_e2e' -or
        $pairing.offers.access -ne 'internal_service_binding_only' -or
        $pairing.offers.ownerAuthorization -ne 'as_owner_session_plus_csrf' -or
        $pairing.offers.serviceCredential -ne 'aud_action_bound_non_oauth' -or
        $pairing.offers.publicOAuthStatus -ne 403 -or
        $pairing.offers.publicDeviceTokenStatus -ne 403 -or
        $pairing.offers.credentialReturnedToBrowser -ne $false -or
        $pairing.exchange.authorization -ne 'high_entropy_one_time_nonce' -or
        $pairing.exchange.acceptsBearer -ne $false -or
        $pairing.exchange.acceptsCallerDeviceId -ne $false -or
        $pairing.exchange.serverAssignedDeviceId -ne $true -or
        $pairing.exchange.requiresAtomicConsume -ne $true -or
        $pairing.exchange.requiresRateLimit -ne $true) {
        Add-ManifestIssue $IssueList $ProjectId (
            'pairing must stay closed and use the fixed service-binding/one-time-nonce contract'
        )
    }
}

function Test-ManifestAgainstRegistry(
    [object]$Manifest,
    [string]$ManifestRaw,
    [object]$Project,
    [object]$Registry,
    [hashtable]$ExpectedRoutes,
    [System.Collections.Generic.List[string]]$IssueList
) {
    $projectId = [string]$Project.id
    foreach ($field in @(
            'standardVersion', 'id', 'name', 'runtimeProject', 'dataPolicy',
            'dataInventory', 'mcp', 'sync', 'knownGaps'
        )) {
        if (-not (Test-HasProperty $Manifest $field)) {
            Add-ManifestIssue $IssueList $projectId "missing required property: $field"
        }
    }

    foreach ($field in @('standardVersion', 'id', 'name', 'runtimeProject', 'dataPolicy')) {
        $expected = if ($field -eq 'standardVersion') { $Registry.standardVersion } else { $Project.$field }
        if ($Manifest.$field -ne $expected) {
            Add-ManifestIssue $IssueList $projectId (
                "$field does not match registry (expected $expected, got $($Manifest.$field))"
            )
        }
    }
    foreach ($capability in @('mcp', 'sync')) {
        if ($Manifest.$capability.status -ne $Project.$capability.status) {
            Add-ManifestIssue $IssueList $projectId (
                "$capability.status does not match registry (expected " +
                "$($Project.$capability.status), got $($Manifest.$capability.status))"
            )
        }
    }
    if (Test-HasProperty $Project.mcp 'dataPlane') {
        if ($Manifest.mcp.dataPlane -ne $Project.mcp.dataPlane) {
            Add-ManifestIssue $IssueList $projectId (
                "mcp.dataPlane does not match registry (expected $($Project.mcp.dataPlane))"
            )
        }
    }
    if (Test-HasProperty $Project.mcp 'toolContractId') {
        if ($Manifest.mcp.toolContractId -ne $Project.mcp.toolContractId) {
            Add-ManifestIssue $IssueList $projectId (
                "mcp.toolContractId must be $($Project.mcp.toolContractId)"
            )
        }
        if (-not (Test-StringSetEqual @($Manifest.mcp.requiredScopes) @($Project.mcp.requiredScopes))) {
            Add-ManifestIssue $IssueList $projectId (
                'mcp.requiredScopes must exactly match the registry'
            )
        }
    }
    if (Test-HasProperty $Project.sync 'dataPlane') {
        if ($Manifest.sync.dataPlane -ne $Project.sync.dataPlane) {
            Add-ManifestIssue $IssueList $projectId (
                "sync.dataPlane does not match registry (expected $($Project.sync.dataPlane))"
            )
        }
    }

    Test-ManifestRoutes $Manifest $ExpectedRoutes $projectId $IssueList
    Test-ManifestInventory $Manifest $projectId $IssueList

    foreach ($field in @('authority', 'supportsPcOff', 'supportsBidirectionalDelta')) {
        if (-not (Test-HasProperty $Manifest.sync $field)) {
            Add-ManifestIssue $IssueList $projectId "sync is missing required property: $field"
        }
    }

    if ($Project.dataPolicy -eq 'local_only') {
        Test-LocalOnlyManifest $Manifest $projectId $IssueList
    }
    if ($projectId -eq 'personal-mcp-gateway') {
        Test-GatewayManifest $Manifest $Project $projectId $IssueList
    }
    if ($projectId -eq 'focuslink') {
        Test-FocusLinkManifest $Manifest $ManifestRaw $Project $projectId $IssueList
    }
    if (Test-HasProperty $Project.sync 'contractId') {
        if ($Manifest.sync.contractId -ne $Project.sync.contractId) {
            Add-ManifestIssue $IssueList $projectId (
                "sync.contractId must be $($Project.sync.contractId)"
            )
        }
    } elseif (
        $Manifest.sync.status -in @('partial', 'complete') -and
        (Test-HasProperty $Manifest.sync 'contractId')
    ) {
        Add-ManifestIssue $IssueList $projectId (
            "manifest declares unregistered sync.contractId $($Manifest.sync.contractId)"
        )
    }
}

function Test-EvidenceClaim(
    [string]$ProjectId,
    [string]$EvidenceId,
    [string]$ExpectedKind,
    [string]$Claim,
    [hashtable]$EvidenceById,
    [hashtable]$EvidenceDocumentById,
    [System.Collections.Generic.List[string]]$GapList
) {
    if (-not $EvidenceById.ContainsKey($EvidenceId) -or
        -not $EvidenceDocumentById.ContainsKey($EvidenceId)) {
        $GapList.Add("$ProjectId/${EvidenceId}: bound evidence is missing or invalid")
        return
    }
    if ($EvidenceById[$EvidenceId].kind -ne $ExpectedKind -or
        ([string]$EvidenceDocumentById[$EvidenceId].claimSha256).ToLowerInvariant() -ne
        (Get-Sha256Text $Claim)) {
        $GapList.Add("$ProjectId/${EvidenceId}: evidence kind or claim binding mismatch")
    }
}

function ConvertTo-WindowsProcessArgument([string]$Argument) {
    if ($Argument -notmatch '[\s"]') { return $Argument }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('').PadLeft(($slashes * 2 + 1), [char]'\'))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append(('').PadLeft($slashes, [char]'\'))
        }
        $slashes = 0
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append(('').PadLeft(($slashes * 2), [char]'\'))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-TrustedEvidenceCommand(
    [object]$Command,
    [string]$Root
) {
    $scriptPath = [string]$Command.scriptPath
    $resolvedScript = [System.IO.Path]::GetFullPath((Join-Path $Root $scriptPath))
    if (-not $resolvedScript.StartsWith(
            $Root + '\', [System.StringComparison]::OrdinalIgnoreCase
        ) -or -not (Test-Path -LiteralPath $resolvedScript -PathType Leaf)) {
        return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'script_missing' }
    }
    $actualHash = (Get-FileHash -LiteralPath $resolvedScript -Algorithm SHA256).Hash
    if ($actualHash -ne ([string]$Command.scriptSha256).ToUpperInvariant()) {
        return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'script_hash_mismatch' }
    }
    $commandInfo = Get-Command ([string]$Command.executable) -CommandType Application `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $commandInfo) {
        return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'executable_missing' }
    }
    $arguments = @($resolvedScript) + @($Command.arguments)
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $commandInfo.Source
    $processInfo.Arguments = @(
        $arguments | ForEach-Object { ConvertTo-WindowsProcessArgument ([string]$_) }
    ) -join ' '
    $processInfo.WorkingDirectory = $Root
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    try {
        if (-not $process.Start()) {
            return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'start_failed' }
        }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([int]$Command.timeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'timeout' }
        }
        $process.WaitForExit()
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        return [pscustomobject]@{
            Ok = $process.ExitCode -eq 0; ExitCode = $process.ExitCode; Reason = ''
        }
    } catch {
        return [pscustomobject]@{ Ok = $false; ExitCode = -1; Reason = 'process_error' }
    } finally {
        $process.Dispose()
    }
}

function Test-CompletionEvidence(
    [object]$Manifest,
    [object]$Project,
    [bool]$RunActiveChecks,
    [System.Collections.Generic.List[string]]$IssueList,
    [System.Collections.Generic.List[string]]$GapList
) {
    $projectId = [string]$Project.id
    $requiresEvidence = $Manifest.sync.status -eq 'complete' -or (
        $Manifest.mcp.status -eq 'complete' -and $Manifest.mcp.dataPlane -ne 'local_only'
    )
    $requiresPcOffEvidence = $Manifest.sync.status -eq 'complete'

    if ($Manifest.sync.status -ne 'complete' -and $Manifest.sync.supportsPcOff -eq $true) {
        Add-ManifestIssue $IssueList $projectId (
            'supportsPcOff cannot be true while sync.status is not complete'
        )
    }
    if ($Manifest.sync.status -eq 'complete' -and $Manifest.sync.supportsPcOff -ne $true) {
        Add-ManifestIssue $IssueList $projectId (
            'sync.status=complete requires supportsPcOff=true'
        )
    }
    if ($Manifest.sync.status -ne 'complete' -and
        $Manifest.sync.supportsBidirectionalDelta -eq $true) {
        Add-ManifestIssue $IssueList $projectId (
            'supportsBidirectionalDelta cannot be true while sync.status is not complete'
        )
    }
    if ($Manifest.sync.status -eq 'complete' -and
        $Manifest.sync.supportsBidirectionalDelta -ne $true) {
        Add-ManifestIssue $IssueList $projectId (
            'sync.status=complete requires supportsBidirectionalDelta=true'
        )
    }
    if ($Manifest.sync.status -eq 'complete' -and @(
            $Manifest.dataInventory | Where-Object { $_.coverage -in @('partial', 'missing') }
        ).Count -gt 0) {
        Add-ManifestIssue $IssueList $projectId (
            'sync.status=complete requires complete-or-exempt dataInventory coverage'
        )
    }
    if ($Manifest.mcp.status -eq 'complete' -and @(
            $Manifest.dataInventory | Where-Object {
                $_.mcpExposure -ne 'none' -and $_.coverage -in @('partial', 'missing')
            }
        ).Count -gt 0) {
        Add-ManifestIssue $IssueList $projectId (
            'mcp.status=complete requires complete-or-exempt exposed inventory coverage'
        )
    }
    if (-not $requiresEvidence) { return }

    $verification = $Manifest.verification
    if ($null -eq $verification) {
        $GapList.Add("${projectId}: complete status has no verification declaration")
        return
    }
    foreach ($field in @(
            'sourceDeployments', 'releaseSetSha256', 'evidenceFiles', 'testCommands',
            'remoteProbes'
        )) {
        if (-not (Test-HasProperty $verification $field)) {
            $GapList.Add("${projectId}: verification is missing $field")
        }
    }

    if ($requiresPcOffEvidence) {
        foreach ($field in @('adbDevices', 'pcOffRounds')) {
            if (-not (Test-HasProperty $verification $field)) {
                $GapList.Add("${projectId}: PC-off verification is missing $field")
            }
        }
    }

    $root = [System.IO.Path]::GetFullPath([string]$Project.manifestRepositoryPath).TrimEnd('\')
    $evidenceById = @{}
    $evidenceDocumentById = @{}
    foreach ($evidence in @($verification.evidenceFiles)) {
        $evidenceId = [string]$evidence.id
        if ([string]::IsNullOrWhiteSpace($evidenceId)) {
            $GapList.Add("${projectId}: evidence file has an empty id")
            continue
        }
        if ($evidenceById.ContainsKey($evidenceId)) {
            $GapList.Add("${projectId}: duplicate evidence id $evidenceId")
            continue
        }
        $evidenceById[$evidenceId] = $evidence
        if ([System.IO.Path]::IsPathRooted([string]$evidence.path)) {
            $GapList.Add("$projectId/${evidenceId}: evidence path must be repository-relative")
            continue
        }
        $evidencePath = [System.IO.Path]::GetFullPath((Join-Path $root $evidence.path))
        if (-not $evidencePath.StartsWith(
                $root + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            $GapList.Add("$projectId/${evidenceId}: evidence path escapes the repository")
            continue
        }
        if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
            $GapList.Add("$projectId/${evidenceId}: evidence file is missing")
            continue
        }
        $actualHash = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash
        if ($actualHash -ne ([string]$evidence.sha256).ToUpperInvariant()) {
            $GapList.Add("$projectId/${evidenceId}: evidence SHA256 mismatch")
        }
        if ([System.IO.Path]::GetExtension($evidencePath) -ne '.json') {
            $GapList.Add("$projectId/${evidenceId}: evidence summary must be JSON")
            continue
        }
        try {
            $document = Get-Content -LiteralPath $evidencePath -Raw -Encoding UTF8 |
                ConvertFrom-Json
        } catch {
            $GapList.Add("$projectId/${evidenceId}: evidence summary is invalid JSON")
            continue
        }
        $allowedEvidenceFields = @(
            'schemaVersion', 'id', 'kind', 'result', 'capturedAt',
            'producerCommandId', 'claimSha256'
        )
        $documentFields = @($document.PSObject.Properties.Name)
        $capturedAt = [DateTimeOffset]::MinValue
        if (-not (Test-StringSetEqual $documentFields $allowedEvidenceFields) -or
            $document.schemaVersion -ne 1 -or
            $document.id -ne $evidenceId -or
            $document.kind -ne $evidence.kind -or
            $document.result -ne 'pass' -or
            -not [DateTimeOffset]::TryParse([string]$document.capturedAt, [ref]$capturedAt) -or
            $capturedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5) -or
            [string]$document.producerCommandId -eq '' -or
            -not (Test-PlausibleSha256 ([string]$document.claimSha256))) {
            $GapList.Add("$projectId/${evidenceId}: evidence summary contract failed")
            continue
        }
        $evidenceDocumentById[$evidenceId] = $document
    }

    $acceptance = if ($requiresPcOffEvidence) {
        $Project.sync.acceptanceEvidence
    } else {
        $Project.mcp.acceptanceEvidence
    }
    if ($null -eq $acceptance) {
        $GapList.Add("${projectId}: complete cloud capability has no centralized acceptanceEvidence")
        return
    }

    $testCommands = @($verification.testCommands)
    $testCommandIds = @($testCommands | ForEach-Object { [string]$_.id })
    foreach ($duplicate in @($testCommandIds | Group-Object | Where-Object Count -gt 1)) {
        $GapList.Add("${projectId}: duplicate test command id $($duplicate.Name)")
    }
    $requiredTestCommands = @($acceptance.requiredTestCommands)
    $requiredTestCommandIds = @($requiredTestCommands | ForEach-Object { [string]$_.id })
    if ($requiredTestCommandIds.Count -eq 0) {
        $IssueList.Add("${projectId}: requiredTestCommands must not be empty")
    } elseif (-not (Test-StringSetEqual $testCommandIds $requiredTestCommandIds)) {
        $GapList.Add("${projectId}: test command set does not match acceptance requirements")
    }
    foreach ($command in $requiredTestCommands) {
        $scriptPath = [string]$command.scriptPath
        $commandText = ([string]$command.executable) + ' ' + $scriptPath + ' ' + (
            @($command.arguments) -join ' '
        )
        if ($command.executable -notin @('node', 'python', 'powershell', 'pwsh') -or
            [System.IO.Path]::IsPathRooted($scriptPath) -or
            $scriptPath -match '(^|[\\/])\.\.([\\/]|$)' -or
            [int]$command.timeoutSeconds -lt 1 -or [int]$command.timeoutSeconds -gt 600 -or
            -not (Test-PlausibleSha256 ([string]$command.scriptSha256)) -or
            $commandText -match '(?i)Bearer\s+[A-Za-z0-9._~-]+' -or
            $commandText -match '(?i)(?:fl2|fla)_[A-Za-z0-9_-]{20,}' -or
            $commandText -match '(?i)--(?:token|secret|nonce|code|cookie)(?:=|\s|$)' -or
            $commandText -match '(?i)Authorization\s*:') {
            $GapList.Add("$projectId/$($command.id): central test command is not trusted")
        }
    }
    foreach ($evidenceId in $evidenceDocumentById.Keys) {
        $producerCommandId = [string]$evidenceDocumentById[$evidenceId].producerCommandId
        if ($producerCommandId -notin $testCommandIds) {
            $GapList.Add("$projectId/${evidenceId}: evidence producer command is not registered")
        }
    }

    $sourceEntries = @($verification.sourceDeployments)
    $sourceIds = @($sourceEntries | ForEach-Object { [string]$_.id })
    foreach ($duplicate in @($sourceIds | Group-Object | Where-Object Count -gt 1)) {
        $GapList.Add("${projectId}: duplicate source deployment id $($duplicate.Name)")
    }
    $requiredSources = @($acceptance.requiredSources)
    $requiredSourceIds = @($requiredSources | ForEach-Object { [string]$_.id })
    if ($requiredSources.Count -eq 0) {
        $IssueList.Add("${projectId}: acceptanceEvidence.requiredSources must not be empty")
    } elseif (-not (Test-StringSetEqual $sourceIds $requiredSourceIds)) {
        $GapList.Add("${projectId}: source deployment set does not match required source set")
    }
    $releaseRecords = @($sourceEntries | Sort-Object id | ForEach-Object {
            @(
                [string]$_.id, [string]$_.kind, [string]$_.commit,
                [string]$_.treeHashAlgorithm, ([string]$_.treeHash).ToLowerInvariant(),
                ([string]$_.deployedVersion).Trim(), [string]$_.deployedAt
            ) -join '|'
        })
    $computedReleaseSetSha256 = Get-Sha256Text (($releaseRecords -join "`n") + "`n")
    if (-not (Test-PlausibleSha256 ([string]$verification.releaseSetSha256)) -or
        ([string]$verification.releaseSetSha256).ToLowerInvariant() -ne
        $computedReleaseSetSha256) {
        $GapList.Add("${projectId}: releaseSetSha256 does not bind the source deployments")
    }
    $latestDeploymentAt = [DateTimeOffset]::MinValue
    foreach ($source in $sourceEntries) {
        $deployedAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse([string]$source.deployedAt, [ref]$deployedAt) -or
            $deployedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5)) {
            $GapList.Add("$projectId/$($source.id): deployment time is invalid")
            continue
        }
        if ($deployedAt -gt $latestDeploymentAt) { $latestDeploymentAt = $deployedAt }
    }
    foreach ($evidenceId in $evidenceDocumentById.Keys) {
        $capturedAt = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse(
                [string]$evidenceDocumentById[$evidenceId].capturedAt,
                [ref]$capturedAt
            ) -and $capturedAt -lt $latestDeploymentAt) {
            $GapList.Add("$projectId/${evidenceId}: evidence predates the release set")
        }
    }
    foreach ($sourceRule in $requiredSources) {
        $sourceId = [string]$sourceRule.id
        $matches = @($sourceEntries | Where-Object id -eq $sourceId)
        if ($matches.Count -ne 1) { continue }
        $entry = $matches[0]
        $sourceEvidenceId = [string]$entry.evidenceId
        if (-not $evidenceById.ContainsKey($sourceEvidenceId)) {
            $GapList.Add("$projectId/${sourceId}: deployment evidence id is missing")
        }
        $deployedVersion = ([string]$entry.deployedVersion).Trim()
        if (-not $deployedVersion -or
            $deployedVersion -match '^(?:unknown|pending|latest|n/?a|tbd|none)$') {
            $GapList.Add("$projectId/${sourceId}: deployedVersion is not verifiable")
        }
        $sourceClaim = @(
            'source', $sourceId, [string]$entry.kind, [string]$entry.commit,
            [string]$entry.treeHashAlgorithm, ([string]$entry.treeHash).ToLowerInvariant(),
            $deployedVersion, [string]$entry.deployedAt, $computedReleaseSetSha256
        ) -join '|'
        Test-EvidenceClaim $projectId $sourceEvidenceId 'source-deployment' (
            $sourceClaim
        ) $evidenceById $evidenceDocumentById $GapList
        if ($entry.kind -ne $sourceRule.kind) {
            $GapList.Add("$projectId/${sourceId}: source kind does not match the registry")
            continue
        }
        $declaredTree = ([string]$entry.treeHash).ToLowerInvariant()
        if ($declaredTree -match '^0+$') {
            $GapList.Add("$projectId/${sourceId}: source tree hash is a placeholder")
            continue
        }
        $sourcePath = [string]$sourceRule.path
        if ($sourceRule.kind -eq 'git') {
            if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
                $GapList.Add("$projectId/${sourceId}: Git source root is missing")
                continue
            }
            $commit = [string]$entry.commit
            if ($commit -notmatch '^[0-9a-fA-F]{7,64}$') {
                $GapList.Add("$projectId/${sourceId}: invalid implementation commit")
                continue
            }
            try {
                & git -C $sourcePath cat-file -e "$commit`^{commit}" 2>$null
            } catch {
                $GapList.Add("$projectId/${sourceId}: implementation commit is missing")
                continue
            }
            if ($LASTEXITCODE -ne 0) {
                $GapList.Add("$projectId/${sourceId}: implementation commit is missing")
                continue
            }
            try {
                $actualTree = [string](& git -C $sourcePath rev-parse "$commit`^{tree}" 2>$null)
            } catch {
                $GapList.Add("$projectId/${sourceId}: source tree cannot be resolved")
                continue
            }
            $actualTree = $actualTree.Trim().ToLowerInvariant()
            $actualAlgorithm = if ($actualTree.Length -eq 64) {
                'git-tree-sha256'
            } else {
                'git-tree-sha1'
            }
            if ($entry.treeHashAlgorithm -ne $actualAlgorithm -or
                $declaredTree -ne $actualTree) {
                $GapList.Add("$projectId/${sourceId}: source tree hash does not match commit")
            }
            try {
                & git -C $sourcePath merge-base --is-ancestor $commit HEAD 2>$null
            } catch {
                $GapList.Add("$projectId/${sourceId}: implementation commit cannot be compared to HEAD")
                continue
            }
            if ($LASTEXITCODE -ne 0) {
                $GapList.Add("$projectId/${sourceId}: implementation commit is not in current HEAD")
            }
            $sourcePathspec = @(
                '.', ':(exclude).poyi/**', ':(exclude)evidence/**',
                ':(exclude)standards/project-platform/reports/**'
            )
            try {
                & git -C $sourcePath diff --quiet $commit HEAD -- @sourcePathspec
                $committedSourceChanged = $LASTEXITCODE -ne 0
                & git -C $sourcePath diff --quiet -- @sourcePathspec
                $workingSourceChanged = $LASTEXITCODE -ne 0
                & git -C $sourcePath diff --cached --quiet -- @sourcePathspec
                $stagedSourceChanged = $LASTEXITCODE -ne 0
                $sourceStatus = @(
                    & git -C $sourcePath status --porcelain --untracked-files=all -- @sourcePathspec
                )
                $untrackedSourceChanged = $sourceStatus.Count -gt 0
            } catch {
                $GapList.Add("$projectId/${sourceId}: source drift check failed")
                continue
            }
            if ($committedSourceChanged -or $workingSourceChanged -or $stagedSourceChanged -or
                $untrackedSourceChanged) {
                $GapList.Add(
                    "$projectId/${sourceId}: source changed after the recorded deployment tree"
                )
            }
        } elseif ($sourceRule.kind -eq 'directory') {
            if ($entry.treeHashAlgorithm -ne 'sha256-path-content-v1') {
                $GapList.Add("$projectId/${sourceId}: directory source uses the wrong hash algorithm")
                continue
            }
            $treeResult = Get-DirectorySourceTreeHash $sourcePath @($sourceRule.includePaths)
            if (-not $treeResult.Ok) {
                $GapList.Add("$projectId/${sourceId}: $($treeResult.Error)")
            } elseif ($declaredTree -ne $treeResult.Hash) {
                $GapList.Add("$projectId/${sourceId}: directory source tree SHA256 mismatch")
            }
        }
    }

    $remoteEntries = @($verification.remoteProbes)
    $remoteIds = @($remoteEntries | ForEach-Object { [string]$_.id })
    foreach ($duplicate in @($remoteIds | Group-Object | Where-Object Count -gt 1)) {
        $GapList.Add("${projectId}: duplicate remote probe id $($duplicate.Name)")
    }
    $approvedBaseUrls = @(
        $Project.publicCloudBaseUrl,
        $Project.mcp.cloudBaseUrl,
        $Project.sync.cloudBaseUrl
    ) | Where-Object { $_ } | Sort-Object -Unique
    foreach ($probe in $remoteEntries) {
        $probeId = [string]$probe.id
        $probeSource = @(
            $sourceEntries | Where-Object id -eq ([string]$probe.sourceDeploymentId)
        )
        $probeDeploymentVersion = if ($probeSource.Count -eq 1) {
            [string]$probeSource[0].deployedVersion
        } else {
            $GapList.Add("$projectId/${probeId}: remote probe source deployment is missing")
            ''
        }
        $probeClaim = @(
            'probe', $probeId, [string]$probe.method, [string]$probe.url,
            [string]$probe.expectedStatus, [string]$probe.sourceDeploymentId,
            $probeDeploymentVersion, $computedReleaseSetSha256
        ) -join '|'
        Test-EvidenceClaim $projectId ([string]$probe.evidenceId) 'remote-probe' (
            $probeClaim
        ) $evidenceById $evidenceDocumentById $GapList
        try {
            $probeUri = [uri]([string]$probe.url)
            $probeOrigin = "$($probeUri.Scheme)://$($probeUri.Authority)"
            $approvedOrigins = @($approvedBaseUrls | ForEach-Object {
                    $uri = [uri]([string]$_)
                    "$($uri.Scheme)://$($uri.Authority)"
                })
            if ($probeUri.Scheme -ne 'https' -or $probeUri.UserInfo -or
                $probeUri.Query -or $probeUri.Fragment -or
                $probeOrigin -notin $approvedOrigins) {
                $GapList.Add("$projectId/${probeId}: remote probe is outside the approved origin")
            }
        } catch {
            $GapList.Add("$projectId/${probeId}: remote probe URL is invalid")
        }
    }
    $requiredRemoteProbes = @($acceptance.requiredRemoteProbes)
    if ($requiredRemoteProbes.Count -eq 0) {
        $IssueList.Add("${projectId}: acceptanceEvidence.requiredRemoteProbes must not be empty")
    }
    foreach ($probeRule in $requiredRemoteProbes) {
        $probeId = [string]$probeRule.id
        $matches = @($remoteEntries | Where-Object id -eq $probeId)
        if ($matches.Count -ne 1) {
            $GapList.Add("${projectId}: required remote probe is missing: $probeId")
            continue
        }
        $probe = $matches[0]
        $baseUrl = [string]($approvedBaseUrls | Select-Object -First 1)
        $expectedUrl = $baseUrl.TrimEnd('/') + [string]$probeRule.path
        if ($probe.method -ne $probeRule.method -or
            [string]$probe.url -ne $expectedUrl -or
            [int]$probe.expectedStatus -ne [int]$probeRule.expectedStatus -or
            $probe.sourceDeploymentId -ne $probeRule.sourceDeploymentId) {
            $GapList.Add("$projectId/${probeId}: remote probe does not match the registry")
        }
    }

    foreach ($requiredId in @($acceptance.requiredEvidenceIds)) {
        if (-not $evidenceById.ContainsKey([string]$requiredId)) {
            $GapList.Add("${projectId}: completion evidence is missing $requiredId")
        }
    }

    $chatGptRule = $acceptance.requiredChatGptMcp
    if ($null -ne $chatGptRule) {
        $chatGptEvidence = $verification.chatgptMcp
        if ($null -eq $chatGptEvidence) {
            $GapList.Add("${projectId}: ChatGPT OAuth MCP evidence is missing")
        } else {
            if ($chatGptEvidence.appId -ne $chatGptRule.appId -or
                $chatGptEvidence.appVersionId -ne $chatGptRule.appVersionId -or
                $chatGptEvidence.mcpUrl -ne $chatGptRule.mcpUrl -or
                $chatGptEvidence.oauthConnected -ne $true -or
                -not (Test-StringSetEqual @($chatGptEvidence.toolNames) @(
                        $chatGptRule.requiredTools
                    ))) {
                $GapList.Add(
                    "${projectId}: ChatGPT evidence does not match the registered OAuth app/version/tools"
                )
            }
            if (-not $evidenceById.ContainsKey([string]$chatGptEvidence.evidenceId)) {
                $GapList.Add("${projectId}: ChatGPT OAuth MCP evidence id is missing")
            }
            $sortedTools = @($chatGptEvidence.toolNames | Sort-Object) -join ','
            $chatGptClaim = @(
                'chatgpt', [string]$chatGptEvidence.appId,
                [string]$chatGptEvidence.appVersionId, [string]$chatGptEvidence.mcpUrl,
                [string]$chatGptEvidence.oauthConnected, $sortedTools,
                $computedReleaseSetSha256
            ) -join '|'
            Test-EvidenceClaim $projectId ([string]$chatGptEvidence.evidenceId) (
                'chatgpt-oauth-mcp'
            ) $chatGptClaim $evidenceById $evidenceDocumentById $GapList
        }
    }

    if ($requiresPcOffEvidence) {
        if ([int]$acceptance.requiredPcOffRounds -ne 3) {
            $IssueList.Add("${projectId}: requiredPcOffRounds must be exactly 3")
        }
        $devices = @($verification.adbDevices)
        $deviceRoles = @($devices | ForEach-Object { [string]$_.role })
        foreach ($duplicate in @($deviceRoles | Group-Object | Where-Object Count -gt 1)) {
            $GapList.Add("${projectId}: duplicate ADB device role $($duplicate.Name)")
        }
        $requiredRoles = @($acceptance.requiredAdbRoles | ForEach-Object { [string]$_ })
        if ($requiredRoles.Count -eq 0) {
            $IssueList.Add("${projectId}: requiredAdbRoles must not be empty")
        } elseif (-not (Test-StringSetEqual $deviceRoles $requiredRoles)) {
            $GapList.Add("${projectId}: ADB physical-device roles do not match acceptance requirements")
        }
        $attestationHashes = @($devices | ForEach-Object {
                ([string]$_.deviceAttestationSha256).ToLowerInvariant()
            })
        if (@($attestationHashes | Group-Object | Where-Object Count -gt 1).Count -gt 0) {
            $GapList.Add("${projectId}: ADB device attestations are not unique")
        }
        foreach ($device in $devices) {
            $role = [string]$device.role
            if ($device.physicalDevice -ne $true -or $device.emulator -ne $false -or
                $device.adbState -ne 'device') {
                $GapList.Add("$projectId/${role}: ADB evidence is not a connected physical device")
            }
            if (-not (Test-PlausibleSha256 ([string]$device.artifactSha256)) -or
                -not (Test-PlausibleSha256 ([string]$device.deviceAttestationSha256))) {
                $GapList.Add("$projectId/${role}: ADB evidence contains a placeholder hash")
            }
            $deviceSource = @(
                $sourceEntries | Where-Object id -eq ([string]$device.sourceDeploymentId)
            )
            if ($deviceSource.Count -ne 1 -or
                ([string]$device.sourceTreeHash).ToLowerInvariant() -ne
                ([string]$deviceSource[0].treeHash).ToLowerInvariant() -or
                ((Test-HasProperty $acceptance 'requiredDeviceSourceDeploymentId') -and
                    $device.sourceDeploymentId -ne $acceptance.requiredDeviceSourceDeploymentId)) {
                $GapList.Add("$projectId/${role}: APK evidence is not bound to the required source tree")
            }
            $deviceClaim = @(
                'adb', $role, [string]$device.packageName, [string]$device.appVersion,
                ([string]$device.artifactSha256).ToLowerInvariant(),
                ([string]$device.deviceAttestationSha256).ToLowerInvariant(),
                [string]$device.sourceDeploymentId,
                ([string]$device.sourceTreeHash).ToLowerInvariant(),
                $computedReleaseSetSha256
            ) -join '|'
            Test-EvidenceClaim $projectId ([string]$device.evidenceId) 'adb-device' (
                $deviceClaim
            ) $evidenceById $evidenceDocumentById $GapList
        }

        $rounds = @($verification.pcOffRounds)
        if ($rounds.Count -ne 3) {
            $GapList.Add("${projectId}: exactly three PC-off rounds are required")
        }
        $roundNumbers = @($rounds | ForEach-Object { [int]$_.round })
        if (-not (Test-StringSetEqual $roundNumbers @(1, 2, 3))) {
            $GapList.Add("${projectId}: PC-off round numbers must be exactly 1, 2, and 3")
        }
        $mutationKinds = @($rounds | ForEach-Object { [string]$_.mutationKind })
        $requiredKinds = @($acceptance.requiredMutationKinds | ForEach-Object { [string]$_ })
        if (-not (Test-StringSetEqual $mutationKinds $requiredKinds)) {
            $GapList.Add("${projectId}: PC-off rounds must cover the required mutation kinds")
        }
        $requiredInteractionRoles = if (Test-HasProperty $acceptance 'requiredInteractionRoles') {
            @($acceptance.requiredInteractionRoles | ForEach-Object { [string]$_ })
        } else {
            $requiredRoles
        }
        $roundSourceRoles = @($rounds | ForEach-Object { [string]$_.sourceDeviceRole })
        if (-not (Test-StringSetEqual $roundSourceRoles $requiredInteractionRoles)) {
            $GapList.Add("${projectId}: PC-off rounds do not cover every required device role")
        }
        $roundEvidenceIds = @($rounds | ForEach-Object { [string]$_.evidenceId })
        if (@($roundEvidenceIds | Group-Object | Where-Object Count -gt 1).Count -gt 0) {
            $GapList.Add("${projectId}: every PC-off round needs distinct evidence")
        }
        $afterRevisions = @()
        foreach ($round in $rounds) {
            $roundId = "pc-off-round-$($round.round)"
            if ($round.pcUnavailable -ne $true -or $round.localServicesStopped -ne $true -or
                $round.mcpReadWhilePcUnavailable -ne $true -or
                $round.restartCatchupVerified -ne $true -or
                $round.exactlyOnceVerified -ne $true) {
                $GapList.Add("$projectId/${roundId}: required PC-off assertions did not all pass")
            }
            $cursorOrder = Compare-SyncCursor (
                [string]$round.cloudRevisionBefore
            ) ([string]$round.cloudRevisionAfter)
            if ($null -eq $cursorOrder -or $cursorOrder -ge 0) {
                $GapList.Add("$projectId/${roundId}: cloud revision is invalid or did not advance")
            }
            $afterRevisions += [string]$round.cloudRevisionAfter
            if ($round.mutationKind -eq 'delete' -and $round.tombstoneVerified -ne $true) {
                $GapList.Add("$projectId/${roundId}: delete round did not verify a tombstone")
            }
            if ([string]$round.sourceDeviceRole -notin $deviceRoles) {
                $GapList.Add("$projectId/${roundId}: source device has no ADB evidence")
            }
            $roundClaim = @(
                'pc-off', [string]$round.round, [string]$round.mutationKind,
                [string]$round.sourceDeviceRole, [string]$round.pcIsolationMode,
                [string]$round.cloudRevisionBefore, [string]$round.cloudRevisionAfter,
                [string]$round.startedAt, [string]$round.finishedAt,
                $computedReleaseSetSha256
            ) -join '|'
            Test-EvidenceClaim $projectId ([string]$round.evidenceId) 'pc-off-round' (
                $roundClaim
            ) $evidenceById $evidenceDocumentById $GapList
            $startedAt = [DateTimeOffset]::MinValue
            $finishedAt = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse([string]$round.startedAt, [ref]$startedAt) -or
                -not [DateTimeOffset]::TryParse([string]$round.finishedAt, [ref]$finishedAt) -or
                $finishedAt -le $startedAt) {
                $GapList.Add("$projectId/${roundId}: timestamps are invalid or unordered")
            }
        }
        if (@($afterRevisions | Group-Object | Where-Object Count -gt 1).Count -gt 0) {
            $GapList.Add("${projectId}: PC-off rounds reused a cloud revision")
        }
        $orderedRounds = @($rounds | Sort-Object { [int]$_.round })
        for ($index = 1; $index -lt $orderedRounds.Count; $index++) {
            if ([string]$orderedRounds[$index].cloudRevisionBefore -ne
                [string]$orderedRounds[$index - 1].cloudRevisionAfter) {
                $GapList.Add("${projectId}: PC-off round revision chain is discontinuous")
                break
            }
            $previousFinishedAt = [DateTimeOffset]::MinValue
            $currentStartedAt = [DateTimeOffset]::MinValue
            if ([DateTimeOffset]::TryParse(
                    [string]$orderedRounds[$index - 1].finishedAt,
                    [ref]$previousFinishedAt
                ) -and [DateTimeOffset]::TryParse(
                    [string]$orderedRounds[$index].startedAt,
                    [ref]$currentStartedAt
                ) -and $currentStartedAt -lt $previousFinishedAt) {
                $GapList.Add("${projectId}: PC-off rounds overlap or are out of order")
                break
            }
        }
    }

    if (-not $RunActiveChecks) { return }

    foreach ($command in $requiredTestCommands) {
        $commandResult = Invoke-TrustedEvidenceCommand $command $root
        if (-not $commandResult.Ok) {
            $GapList.Add(
                "$projectId/$($command.id): trusted test command failed ($($commandResult.Reason))"
            )
        }
    }

    foreach ($probe in @($verification.remoteProbes)) {
        $actualStatus = Get-HttpStatus ([string]$probe.url) ([string]$probe.method)
        if ($actualStatus -ne [int]$probe.expectedStatus) {
            $GapList.Add(
                "$projectId/$($probe.id): remote probe returned $actualStatus, expected " +
                "$($probe.expectedStatus)"
            )
        }
    }
    # Commands are not trusted to leave their inputs untouched; revalidate all static bindings.
    Test-CompletionEvidence $Manifest $Project $false $IssueList $GapList
}

function Test-OAuthOwnerTrust(
    [object]$Registry,
    [System.Collections.Generic.List[string]]$IssueList,
    [System.Collections.Generic.List[string]]$GapList
) {
    $policy = $Registry.oauthOwnerTrust
    if ($null -eq $policy) { return }

    $expectedScopes = @('journal:read', 'journal:write', 'focuslink:read', 'watch:read')
    if ($policy.trustRoot -ne 'cloudflare_account_and_local_authenticated_wrangler' -or
        $policy.loginCodeAuthority -ne 'd1_owner_login_codes' -or
        $policy.grantAuthority -ne 'sqlite_durable_object' -or
        $policy.plaintextPolicy -ne 'stdout_once_never_env_argv_sql_or_log' -or
        -not (Test-StringSetEqual @($policy.canonicalScopes) $expectedScopes) -or
        $policy.oidc -ne $false) {
        $IssueList.Add('OAuth owner trust policy does not match the fixed platform contract')
    }

    $root = [string]$policy.repositoryPath
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        $GapList.Add('oauth-owner-trust: repository path is missing')
        return
    }
    $issuerScript = Join-Path $root ([string]$policy.issuerScript)
    if (-not (Test-Path -LiteralPath $issuerScript -PathType Leaf)) {
        $GapList.Add('oauth-owner-trust: scripts/issue-owner-code.mjs is missing')
    } else {
        $issuerSource = Get-Content -LiteralPath $issuerScript -Raw -Encoding UTF8
        $randomProof = $issuerSource -match 'randomBytes\s*\(\s*32\s*\)' -or
            $issuerSource -match 'getRandomValues'
        if (-not $randomProof -and $issuerSource -match 'createOwnerCode') {
            $helper = Join-Path (Split-Path -Parent $issuerScript) 'owner-code-lib.mjs'
            if (Test-Path -LiteralPath $helper -PathType Leaf) {
                $helperSource = Get-Content -LiteralPath $helper -Raw -Encoding UTF8
                $randomProof = (
                    $helperSource -match 'randomBytes' -and
                    $helperSource -match 'random\s*\(\s*32\s*\)'
                )
            }
        }
        if (-not $randomProof) {
            $GapList.Add('oauth-owner-trust: issuer script does not generate 32 random bytes')
        }
        if ($issuerSource -match 'process\.env\.[A-Z0-9_]*(CODE|TICKET)' -or
            $issuerSource -match 'process\.argv\[[0-9]+\].*(code|ticket)') {
            $GapList.Add('oauth-owner-trust: plaintext code must not enter env or argv')
        }
    }

    $scanRoots = @('src', 'migrations', 'scripts')
    $sourceFiles = @()
    foreach ($relativeRoot in $scanRoots) {
        $scanRoot = Join-Path $root $relativeRoot
        if (Test-Path -LiteralPath $scanRoot -PathType Container) {
            $sourceFiles += Get-ChildItem -LiteralPath $scanRoot -Recurse -File | Where-Object {
                $_.Extension -in @('.ts', '.js', '.mjs', '.sql', '.json', '.jsonc')
            }
        }
    }
    $forbiddenPatterns = @(
        'PBKDF2_ITERATIONS',
        'OAUTH_SEED_PASSWORD',
        'issueLoginCode',
        'password_hash',
        'password_salt',
        'password_iterations'
    )
    foreach ($pattern in $forbiddenPatterns) {
        $match = @($sourceFiles | Select-String -Pattern $pattern -SimpleMatch | Select-Object -First 1)
        if ($match.Count -gt 0) {
            $GapList.Add(
                "oauth-owner-trust: forbidden owner password/issuance pattern remains: $pattern"
            )
        }
    }

    $indexPath = Join-Path $root 'src\index.ts'
    if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
        $GapList.Add('oauth-conformance: src/index.ts is missing')
    } else {
        $indexSource = Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8
        if ($indexSource -notmatch 'export[\s\S]{0,120}\bOAuthState\b') {
            $GapList.Add('oauth-conformance: src/index.ts does not export OAuthState')
        }
        if ($indexSource -match 'watch:write' -or
            $indexSource -match 'foxlink:read' -or
            $indexSource -match 'focuslink:pair' -or
            $indexSource -match 'devices:manage') {
            $GapList.Add('oauth-conformance: forbidden scope is present in authorization metadata')
        }
    }

    $wranglerConfigs = @(
        Get-ChildItem -LiteralPath $root -File -Filter 'wrangler*.jsonc' -ErrorAction SilentlyContinue
    )
    if ($wranglerConfigs.Count -eq 0) {
        $GapList.Add('oauth-conformance: Wrangler configuration is missing')
    }
    foreach ($config in $wranglerConfigs) {
        $configSource = Get-Content -LiteralPath $config.FullName -Raw -Encoding UTF8
        $dateMatch = [regex]::Match(
            $configSource,
            '"compatibility_date"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})"'
        )
        if (-not $dateMatch.Success -or
            [datetime]$dateMatch.Groups[1].Value -gt [datetime]$policy.compatibilityDateMaximum) {
            $GapList.Add(
                "oauth-conformance: $($config.Name) has a missing or future compatibility_date"
            )
        }
        if ($configSource -notmatch '\bOAuthState\b') {
            $GapList.Add("oauth-conformance: $($config.Name) does not bind OAuthState")
        }
    }
}

function Test-ProtocolContracts(
    [object]$Registry,
    [string]$RegistryDirectory,
    [System.Collections.Generic.List[string]]$IssueList,
    [System.Collections.Generic.List[string]]$GapList
) {
    $contracts = @($Registry.protocolContracts | Where-Object { $null -ne $_ })
    $contractIds = @($contracts | ForEach-Object { $_.id })
    foreach ($duplicate in @($contractIds | Group-Object | Where-Object Count -gt 1)) {
        $IssueList.Add("Duplicate protocol contract id: $($duplicate.Name)")
    }

    foreach ($contract in $contracts) {
        $artifactFields = @(
            'schemaRelativePath',
            'requestFixtureRelativePath',
            'responseFixtureRelativePath'
        )
        $canonicalHashes = @{}
        foreach ($field in $artifactFields) {
            $relativePath = [string]$contract.$field
            $path = Join-Path $RegistryDirectory $relativePath
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                $IssueList.Add("$($contract.id): missing canonical contract artifact $relativePath")
                continue
            }
            try {
                Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
            } catch {
                $IssueList.Add("$($contract.id): invalid JSON in canonical artifact $relativePath")
                continue
            }
            $canonicalHashes[$field] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        }

        $projectIds = if (Test-HasProperty $contract 'projectIds') {
            @($contract.projectIds)
        } else {
            @($contract.projectId)
        }
        if ($projectIds.Count -lt 1 -or @($projectIds | Where-Object { $_ -isnot [string] -or $_.Length -lt 1 }).Count -gt 0) {
            $IssueList.Add("$($contract.id): projectIds must contain one or more project ids")
        }
        foreach ($projectId in $projectIds) {
            $project = @($Registry.projects | Where-Object id -eq $projectId)
            if ($project.Count -ne 1 -or $project[0].sync.contractId -ne $contract.id) {
                $IssueList.Add(
                    "$($contract.id): project $projectId must register sync.contractId"
                )
            }
        }

        foreach ($consumer in @($contract.consumers)) {
            if (-not (Test-Path -LiteralPath $consumer.repositoryPath -PathType Container)) {
                $GapList.Add(
                    "$($contract.id)/$($consumer.id): consumer repository path is missing"
                )
                continue
            }
            foreach ($field in $artifactFields) {
                if (-not $canonicalHashes.ContainsKey($field)) { continue }
                $consumerPath = Join-Path $consumer.repositoryPath $consumer.$field
                if (-not (Test-Path -LiteralPath $consumerPath -PathType Leaf)) {
                    $GapList.Add(
                        "$($contract.id)/$($consumer.id): missing $($consumer.$field)"
                    )
                    continue
                }
                $consumerHash = (Get-FileHash -LiteralPath $consumerPath -Algorithm SHA256).Hash
                if ($consumerHash -ne $canonicalHashes[$field]) {
                    $IssueList.Add(
                        "$($contract.id)/$($consumer.id): $field differs from canonical artifact"
                    )
                }
            }
        }

        foreach ($projectId in $projectIds) {
            $mappedConsumers = @(
                $contract.consumers | Where-Object {
                    (Test-HasProperty $_ 'projectIds') -and
                    $projectId -in @($_.projectIds)
                }
            )
            if ($mappedConsumers.Count -eq 0) {
                $GapList.Add(
                    "$($contract.id): project $projectId has no registered contract consumer"
                )
            }
        }
    }

    foreach ($project in @(
            $Registry.projects | Where-Object {
                $_.lifecycle -eq 'active' -and
                $_.runtimeProject -eq $true -and
                $_.sync.status -in @('partial', 'complete') -and
                (Test-HasProperty $_.sync 'contractId')
            }
        )) {
        $matches = @(
            $contracts | Where-Object id -eq $project.sync.contractId
        )
        if ($matches.Count -ne 1 -or $project.id -notin @($matches[0].projectIds)) {
            $GapList.Add(
                "$($project.id): sync.contractId $($project.sync.contractId) is not registered for the project"
            )
        }
    }
}

function Test-McpContracts(
    [object]$Registry,
    [string]$RegistryDirectory,
    [System.Collections.Generic.List[string]]$IssueList,
    [System.Collections.Generic.List[string]]$GapList
) {
    $contractIds = @($Registry.mcpContracts | ForEach-Object { $_.id })
    foreach ($duplicate in @($contractIds | Group-Object | Where-Object Count -gt 1)) {
        $IssueList.Add("Duplicate MCP contract id: $($duplicate.Name)")
    }

    foreach ($contract in @($Registry.mcpContracts)) {
        $project = @($Registry.projects | Where-Object id -eq $contract.projectId)
        if ($project.Count -ne 1) {
            $IssueList.Add("$($contract.id): unknown project $($contract.projectId)")
            continue
        }
        if ($project[0].mcp.toolContractId -ne $contract.id) {
            $IssueList.Add(
                "$($contract.id): project $($contract.projectId) must register mcp.toolContractId"
            )
        }

        $requiredScopes = @($contract.requiredScopes)
        $projectScopes = @($project[0].mcp.requiredScopes)
        $canonicalScopes = @($Registry.oauthOwnerTrust.canonicalScopes)
        if (
            $requiredScopes.Count -eq 0 -or
            (Compare-Object $requiredScopes $projectScopes) -or
            @($requiredScopes | Where-Object { $_ -notin $canonicalScopes }).Count -gt 0
        ) {
            $IssueList.Add(
                "$($contract.id): required scopes must exactly match the registered canonical OAuth scopes"
            )
        }

        $tools = @($contract.canonicalTools)
        if (
            $tools.Count -eq 0 -or
            @($tools | Group-Object | Where-Object Count -gt 1).Count -gt 0 -or
            @($tools | Where-Object { $_ -notmatch '^focuslink_[a-z0-9_]+$' }).Count -gt 0
        ) {
            $IssueList.Add("$($contract.id): canonical tool names are missing, duplicate, or invalid")
        }
        foreach ($alias in @($contract.compatibilityAliases.psobject.Properties)) {
            if ($alias.Name -notmatch '^foxlink_[a-z0-9_]+$' -or $alias.Value -notin $tools) {
                $IssueList.Add("$($contract.id): invalid compatibility alias $($alias.Name)")
            }
        }

        $artifactFields = @(
            'schemaRelativePath',
            'requestFixtureRelativePath',
            'responseFixtureRelativePath'
        )
        $canonicalHashes = @{}
        foreach ($field in $artifactFields) {
            $relativePath = [string]$contract.$field
            $path = Join-Path $RegistryDirectory $relativePath
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                $IssueList.Add("$($contract.id): missing canonical MCP artifact $relativePath")
                continue
            }
            try {
                Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
            } catch {
                $IssueList.Add("$($contract.id): invalid JSON in canonical MCP artifact $relativePath")
                continue
            }
            $canonicalHashes[$field] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        }

        $consumer = $contract.consumer
        if (-not (Test-Path -LiteralPath $consumer.repositoryPath -PathType Container)) {
            $GapList.Add("$($contract.id)/$($consumer.id): consumer repository path is missing")
            continue
        }
        foreach ($field in $artifactFields) {
            if (-not $canonicalHashes.ContainsKey($field)) { continue }
            $consumerPath = Join-Path $consumer.repositoryPath $consumer.$field
            if (-not (Test-Path -LiteralPath $consumerPath -PathType Leaf)) {
                $GapList.Add("$($contract.id)/$($consumer.id): missing $($consumer.$field)")
                continue
            }
            $consumerHash = (Get-FileHash -LiteralPath $consumerPath -Algorithm SHA256).Hash
            if ($consumerHash -ne $canonicalHashes[$field]) {
                $IssueList.Add("$($contract.id)/$($consumer.id): $field differs from canonical artifact")
            }
        }
    }
}

$resolvedRegistry = (Resolve-Path -LiteralPath $RegistryPath).Path
$registryDirectory = Split-Path -Parent $resolvedRegistry
$registry = Get-Content -LiteralPath $resolvedRegistry -Raw -Encoding UTF8 | ConvertFrom-Json
$manifestSchemaPath = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\project-platform.schema.json')
)
$manifestValidatorScript = Join-Path $PSScriptRoot 'validate-project-manifest.py'
$manifestValidatorPython = Get-ManifestValidatorPython
$issues = New-Object System.Collections.Generic.List[string]
$manifestGaps = New-Object System.Collections.Generic.List[string]
$contractGaps = New-Object System.Collections.Generic.List[string]
$evidenceGaps = New-Object System.Collections.Generic.List[string]
$capabilityGaps = New-Object System.Collections.Generic.List[string]
$runActiveEvidenceChecks = [bool]($VerifyEvidence -or ($Strict -and $Live))
$securityBlockers = @($registry.securityFindings | Where-Object {
    $_.blocksRelease -eq $true -and $_.status -ne 'resolved'
})

if (-not $manifestValidatorPython) {
    $issues.Add('Python is required to execute the project manifest JSON Schema validator')
} elseif (-not (Test-Path -LiteralPath $manifestValidatorScript -PathType Leaf)) {
    $issues.Add("Manifest schema validator is missing: $manifestValidatorScript")
}

if ($registry.standardVersion -ne 1) {
    $issues.Add("Unsupported registry standardVersion: $($registry.standardVersion)")
}

$expectedRoutes = @{
    mcp = '/mcp'
    health = '/healthz'
    ready = '/readyz'
    oauthResourceMetadata = '/.well-known/oauth-protected-resource/mcp'
    syncExchange = '/sync/v2/exchange'
    syncStatus = '/sync/v2/status'
    pairOffers = '/sync/v1/pair/offers'
    pairExchange = '/sync/v1/pair/exchange'
}
foreach ($routeName in $expectedRoutes.Keys) {
    if ($registry.canonicalRoutes.$routeName -ne $expectedRoutes[$routeName]) {
        $issues.Add(
            "Canonical route $routeName must be $($expectedRoutes[$routeName]), got " +
            "$($registry.canonicalRoutes.$routeName)"
        )
    }
}

$projectIds = @($registry.projects | ForEach-Object { $_.id })
$duplicateIds = @($projectIds | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
foreach ($id in $duplicateIds) { $issues.Add("Duplicate project id: $id") }

$repositoryUrls = @($registry.projects | ForEach-Object { @($_.repositoryUrls) })
if ($repositoryUrls.Count -ne [int]$registry.scope.officialRepositoryCount) {
    $issues.Add(
        "Official repository count is $($repositoryUrls.Count), expected " +
        "$($registry.scope.officialRepositoryCount)"
    )
}

$activeProjects = @($registry.projects | Where-Object {
    $_.runtimeProject -and $_.lifecycle -eq 'active'
})
if (Test-HasProperty $registry.scope 'activeRuntimeProjectCount') {
    if ($activeProjects.Count -ne [int]$registry.scope.activeRuntimeProjectCount) {
        $issues.Add(
            "Active runtime project count is $($activeProjects.Count), expected " +
            "$($registry.scope.activeRuntimeProjectCount)"
        )
    }
}

$manifestRows = @()
foreach ($project in $registry.projects) {
    if (-not $project.id -or -not $project.name) {
        $issues.Add('Every project must have id and name')
        continue
    }
    if ($project.mcp.status -notin @('complete', 'partial', 'missing', 'exempt')) {
        $issues.Add("$($project.id): invalid MCP status $($project.mcp.status)")
    }
    if ($project.sync.status -notin @('complete', 'partial', 'missing', 'exempt')) {
        $issues.Add("$($project.id): invalid sync status $($project.sync.status)")
    }

    $paths = @($project.repositoryPaths)
    if ($project.lifecycle -eq 'active' -and $paths.Count -eq 0) {
        $issues.Add("$($project.id): active project has no repository path")
    }
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            $issues.Add("$($project.id): repository path is missing: $path")
            continue
        }
        $gitDirectory = Join-Path $path '.git'
        if ((Test-Path -LiteralPath (Join-Path $gitDirectory 'HEAD')) -and
            (Test-Path -LiteralPath (Join-Path $gitDirectory 'config'))) {
            try {
                $remote = (& git -C $path remote get-url origin 2>$null | Select-Object -First 1)
            } catch {
                $remote = $null
            }
            if ($remote) {
                $allowed = @($project.repositoryUrls | ForEach-Object {
                    Normalize-RepositoryUrl $_
                })
                if ((Normalize-RepositoryUrl $remote) -notin $allowed) {
                    $issues.Add("$($project.id): unexpected origin remote at $path")
                }
            }
        }
    }

    $manifestFound = $false
    $manifestValid = $false
    $manifestSupportsPcOff = $false
    $manifestRequiresEvidence = $false
    $manifestEvidenceVerified = $false
    $manifestCompletionEligible = $false
    $manifestPath = ''
    if ($project.lifecycle -eq 'active') {
        if (-not $project.manifestRepositoryPath) {
            $issues.Add("$($project.id): active project is missing manifestRepositoryPath")
        } elseif ((Normalize-PathValue $project.manifestRepositoryPath) -notin @(
                $paths | ForEach-Object { Normalize-PathValue $_ }
            )) {
            $issues.Add("$($project.id): manifestRepositoryPath is not in repositoryPaths")
        } elseif (Test-Path -LiteralPath $project.manifestRepositoryPath -PathType Container) {
            $manifestPath = Join-Path $project.manifestRepositoryPath $project.manifestRelativePath
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
                $manifestGaps.Add(
                    "$($project.id): missing $manifestPath"
                )
            } else {
                $manifestFound = $true
                try {
                    $issueCountBefore = $issues.Count
                    $evidenceGapCountBefore = $evidenceGaps.Count
                    $manifestRaw = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
                    $manifestHashBefore = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
                    $manifest = $manifestRaw | ConvertFrom-Json
                    $manifestSupportsPcOff = $manifest.sync.supportsPcOff -eq $true
                    $manifestRequiresEvidence = $manifest.sync.status -eq 'complete' -or (
                        $manifest.mcp.status -eq 'complete' -and
                        $manifest.mcp.dataPlane -ne 'local_only'
                    )
                    if ($manifestValidatorPython) {
                        $schemaOutput = @(
                            & $manifestValidatorPython $manifestValidatorScript (
                                $manifestSchemaPath
                            ) $manifestPath 2>&1
                        )
                        if ($LASTEXITCODE -ne 0) {
                            $schemaMessage = ($schemaOutput | Select-Object -First 5) -join '; '
                            Add-ManifestIssue $issues ([string]$project.id) (
                                "JSON Schema validation failed: $schemaMessage"
                            )
                        }
                    }
                    Test-ManifestAgainstRegistry (
                        $manifest
                    ) $manifestRaw $project $registry $expectedRoutes $issues
                    Test-CompletionEvidence (
                        $manifest
                    ) $project $runActiveEvidenceChecks (
                        $issues
                    ) $evidenceGaps
                    if ($runActiveEvidenceChecks -and
                        $manifestHashBefore -ne (
                            Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256
                        ).Hash) {
                        $evidenceGaps.Add(
                            "$($project.id): manifest changed while active evidence commands were running"
                        )
                    }
                    $manifestValid = (
                        $issues.Count -eq $issueCountBefore -and
                        $evidenceGaps.Count -eq $evidenceGapCountBefore
                    )
                    $manifestEvidenceVerified = (
                        $manifestRequiresEvidence -and $runActiveEvidenceChecks -and
                        $manifestValid
                    )
                    $manifestCompletionEligible = $manifestValid -and (
                        -not $manifestRequiresEvidence -or $manifestEvidenceVerified
                    )
                } catch {
                    $issues.Add(
                        "$($project.id) manifest validation failed ($($_.Exception.GetType().Name)): " +
                        $_.Exception.Message
                    )
                }
            }
        }
    }

    if ($project.runtimeProject -and $project.lifecycle -eq 'active') {
        if ($project.mcp.status -notin @('complete', 'exempt')) {
            $capabilityGaps.Add("$($project.id): MCP is $($project.mcp.status)")
        }
        if ($project.sync.status -notin @('complete', 'exempt')) {
            $capabilityGaps.Add("$($project.id): sync is $($project.sync.status)")
        }
    }

    $manifestRows += [pscustomobject]@{
        Id = $project.id
        Found = $manifestFound
        Valid = $manifestValid
        SupportsPcOff = $manifestSupportsPcOff
        RequiresEvidence = $manifestRequiresEvidence
        EvidenceVerified = $manifestEvidenceVerified
        CompletionEligible = $manifestCompletionEligible
        Path = $manifestPath
    }
}

if (
    $registry.protocolContracts -or
    @(
        $registry.projects | Where-Object {
            $_.lifecycle -eq 'active' -and
            $_.runtimeProject -eq $true -and
            (Test-HasProperty $_.sync 'contractId')
        }
    ).Count -gt 0
) {
    Test-ProtocolContracts $registry $registryDirectory $issues $contractGaps
}
if ($registry.mcpContracts) {
    Test-McpContracts $registry $registryDirectory $issues $contractGaps
}
Test-OAuthOwnerTrust $registry $issues $contractGaps

$focusProject = @($registry.projects | Where-Object id -eq 'focuslink')
if ($focusProject.Count -eq 1) {
    $focus = $focusProject[0]
    $fixedFoxlinkOrigin = 'https://foxlink-mcp.focuslink-poyi-6465e9.workers.dev'
    if ($focus.publicCloudBaseUrl -ne $fixedFoxlinkOrigin -or
        $focus.mcp.cloudBaseUrl -ne $fixedFoxlinkOrigin -or
        $focus.sync.cloudBaseUrl -ne $fixedFoxlinkOrigin) {
        $issues.Add('focuslink: registry must use one foxlink public cloudBaseUrl')
    }
    if ($focus.internalAuthority.exposure -ne 'edge_contained_noncanonical' -or
        $focus.internalAuthority.publiclyReachable -ne $false -or
        $focus.internalAuthority.publicCanonical -ne $false -or
        $focus.internalAuthority.private -ne $false -or
        $focus.internalAuthority.registeredInManifest -ne $false) {
        $issues.Add(
            'focuslink: upstream must remain honestly edge-contained, noncanonical, and not private'
        )
    }
    $acceptance = $focus.sync.acceptanceEvidence
    $requiredSourceKinds = @{
        'focuslink-client' = 'git'
        'foxlink-cloud-mcp' = 'git'
        'poyi-oauth-as' = 'directory'
    }
    $requiredSourcePaths = @{
        'focuslink-client' = [string]$focus.manifestRepositoryPath
        'foxlink-cloud-mcp' = 'C:\开发\mcp开发\foxlink-cloud-mcp'
        'poyi-oauth-as' = [string]$registry.oauthOwnerTrust.repositoryPath
    }
    $sourceRules = @($acceptance.requiredSources)
    $sourcePolicyValid = $sourceRules.Count -eq $requiredSourceKinds.Count
    foreach ($sourceId in $requiredSourceKinds.Keys) {
        $match = @($sourceRules | Where-Object id -eq $sourceId)
        $sourcePolicyValid = $sourcePolicyValid -and $match.Count -eq 1 -and
            $match[0].kind -eq $requiredSourceKinds[$sourceId] -and
            (Normalize-PathValue ([string]$match[0].path)) -eq
            (Normalize-PathValue $requiredSourcePaths[$sourceId])
    }
    $oauthSource = @($sourceRules | Where-Object id -eq 'poyi-oauth-as')
    $sourcePolicyValid = $sourcePolicyValid -and $oauthSource.Count -eq 1 -and
        (Test-StringSetEqual @($oauthSource[0].includePaths) @(
                'src', 'scripts', 'migrations', 'tests', 'package.json',
                'package-lock.json', 'tsconfig.json', 'vitest.config.mjs',
                'wrangler.jsonc', 'wrangler.test.jsonc', 'README.md'
            ))
    $requiredProbePolicy = @{
        health = @('GET', '/healthz', 200, 'foxlink-cloud-mcp')
        ready = @('GET', '/readyz', 200, 'foxlink-cloud-mcp')
        'oauth-resource-metadata' = @(
            'GET', '/.well-known/oauth-protected-resource/mcp', 200, 'foxlink-cloud-mcp'
        )
        'sync-status-auth-challenge' = @(
            'GET', '/sync/v2/status', 401, 'foxlink-cloud-mcp'
        )
    }
    $probeRules = @($acceptance.requiredRemoteProbes)
    $probePolicyValid = $probeRules.Count -eq $requiredProbePolicy.Count
    foreach ($probeId in $requiredProbePolicy.Keys) {
        $match = @($probeRules | Where-Object id -eq $probeId)
        $expected = $requiredProbePolicy[$probeId]
        $probePolicyValid = $probePolicyValid -and $match.Count -eq 1 -and
            $match[0].method -eq $expected[0] -and
            $match[0].path -eq $expected[1] -and
            [int]$match[0].expectedStatus -eq [int]$expected[2] -and
            $match[0].sourceDeploymentId -eq $expected[3]
    }
    $requiredTestCommandPolicy = @{
        'verify-source-deployments' = @(
            'node', 'scripts/release-evidence/verify-source-deployments.mjs', 120
        )
        'verify-remote-probes' = @(
            'node', 'scripts/release-evidence/verify-remote-probes.mjs', 120
        )
        'verify-adb-physical-devices' = @(
            'node', 'scripts/release-evidence/verify-adb-physical-devices.mjs', 180
        )
        'verify-pc-off-rounds' = @(
            'node', 'scripts/release-evidence/verify-pc-off-rounds.mjs', 300
        )
        'verify-chatgpt-oauth-mcp' = @(
            'node', 'scripts/release-evidence/verify-chatgpt-oauth-mcp.mjs', 180
        )
    }
    $testCommandRules = @($acceptance.requiredTestCommands)
    $testCommandPolicyValid = $testCommandRules.Count -eq $requiredTestCommandPolicy.Count
    foreach ($commandId in $requiredTestCommandPolicy.Keys) {
        $match = @($testCommandRules | Where-Object id -eq $commandId)
        $expected = $requiredTestCommandPolicy[$commandId]
        $testCommandPolicyValid = $testCommandPolicyValid -and $match.Count -eq 1 -and
            $match[0].executable -eq $expected[0] -and
            $match[0].scriptPath -eq $expected[1] -and
            [int]$match[0].timeoutSeconds -eq [int]$expected[2] -and
            @($match[0].arguments).Count -eq 0
    }
    $chatGptPolicy = $acceptance.requiredChatGptMcp
    if (-not $sourcePolicyValid -or -not $probePolicyValid -or -not $testCommandPolicyValid -or
        -not (Test-StringSetEqual @($acceptance.requiredAdbRoles) @(
                'phone', 'tablet', 'watch'
            )) -or
        $acceptance.requiredDeviceSourceDeploymentId -ne 'focuslink-client' -or
        -not (Test-StringSetEqual @($acceptance.requiredInteractionRoles) @(
                'phone', 'tablet', 'watch'
            )) -or
        [int]$acceptance.requiredPcOffRounds -ne 3 -or
        -not (Test-StringSetEqual @($acceptance.requiredMutationKinds) @(
                'create', 'update', 'delete'
            )) -or
        -not (Test-StringSetEqual @($acceptance.requiredEvidenceIds) @(
                'chatgpt-oauth-mcp'
            )) -or
        $chatGptPolicy.appId -ne 'asdk_app_6a6863c3636481919adeb26f92d8546c' -or
        $chatGptPolicy.appVersionId -ne 'asdk_app_v_6a6863c4c3808191b17156ce1cea8606' -or
        $chatGptPolicy.mcpUrl -ne "$fixedFoxlinkOrigin/mcp" -or
        -not (Test-StringSetEqual @($chatGptPolicy.requiredTools) @(
                'focuslink_get_status', 'focuslink_get_today_summary',
                'focuslink_list_focus_records', 'focuslink_get_task_summary'
            ))) {
        $issues.Add('focuslink: acceptance-evidence policy is weaker than the release gate')
    }
    if ($focus.sync.pairing.status -ne 'closed_pending_as_binding_and_joint_e2e' -or
        $focus.sync.pairing.offers.access -ne 'internal_service_binding_only' -or
        $focus.sync.pairing.offers.serviceCredential -ne 'aud_action_bound_non_oauth' -or
        $focus.sync.pairing.exchange.acceptsBearer -ne $false -or
        $focus.sync.pairing.exchange.acceptsCallerDeviceId -ne $false) {
        $issues.Add('focuslink: registry pairing contract has drifted from the fixed closed state')
    }
    $foxlinkService = @($registry.supportServices | Where-Object id -eq 'foxlink-cloud-mcp')
    $upstreamService = @(
        $registry.supportServices | Where-Object id -eq 'focuslink-device-sync-worker'
    )
    $focusManifestValid = @(
        $manifestRows | Where-Object { $_.Id -eq 'focuslink' -and $_.Valid }
    ).Count -eq 1
    $focusReleaseClaimed = (
        $focus.mcp.status -eq 'complete' -and $focus.sync.status -eq 'complete' -and
        $focusManifestValid
    )
    if ($foxlinkService.Count -eq 1) {
        $supportManifestPath = Join-Path $foxlinkService[0].path '.poyi\project-platform.json'
        if (-not (Test-Path -LiteralPath $supportManifestPath -PathType Leaf)) {
            $issues.Add('focuslink: foxlink support manifest is missing')
        } else {
            try {
                $supportManifest = Get-Content -LiteralPath $supportManifestPath -Raw `
                    -Encoding UTF8 | ConvertFrom-Json
                if ($supportManifest.sync.routes.exchange -ne '/sync/v2/exchange' -or
                    $supportManifest.sync.routes.status -ne '/sync/v2/status' -or
                    $supportManifest.sync.routes.pairOffers -ne '/sync/v1/pair/offers' -or
                    $supportManifest.sync.routes.pairExchange -ne '/sync/v1/pair/exchange' -or
                    $supportManifest.sync.contractId -ne 'sync-envelope-v1' -or
                    $supportManifest.sync.envelopeVersion -ne 1 -or
                    $supportManifest.sync.cloudBaseUrl -ne $fixedFoxlinkOrigin) {
                    $issues.Add(
                        'focuslink: foxlink support manifest must register the canonical v2 routes'
                    )
                }
            } catch {
                $issues.Add('focuslink: foxlink support manifest is invalid JSON')
            }
        }
    }
    if ($foxlinkService.Count -ne 1 -or
        $foxlinkService[0].role -ne 'canonical_public_gateway' -or
        $foxlinkService[0].publicCanonical -ne $true -or
        $foxlinkService[0].registrationTarget -ne $true -or
        $foxlinkService[0].registeredInManifest -ne $focusManifestValid -or
        $foxlinkService[0].canonicalContractDeployed -ne $focusReleaseClaimed -or
        $foxlinkService[0].deployed -ne $focusReleaseClaimed -or
        $foxlinkService[0].health -ne "$fixedFoxlinkOrigin/healthz") {
        $issues.Add(
            'focuslink: foxlink canonical registration/deployment flags do not match evidence'
        )
    }
    if ($upstreamService.Count -ne 1 -or
        $upstreamService[0].role -ne 'internal_authoritative_upstream' -or
        $upstreamService[0].exposure -ne 'edge_contained_noncanonical' -or
        $upstreamService[0].publiclyReachable -ne $false -or
        $upstreamService[0].publicCanonical -ne $false -or
        $upstreamService[0].private -ne $false -or
        $upstreamService[0].registeredInManifest -ne $false -or
        $upstreamService[0].health -or
        $upstreamService[0].observedReachability.legacyDomainStatus -ne 530 -or
        $upstreamService[0].observedReachability.workersDevBypassStatus -ne 404) {
        $issues.Add(
            'focuslink: DO support entry must stay noncanonical and must not register a health URL'
        )
    }
}

$watchProject = @($registry.projects | Where-Object id -eq 'watchintervals')
if ($watchProject.Count -eq 1) {
    $watch = $watchProject[0]
    if ($watch.sync.status -eq 'partial' -and (
        $watch.sync.dataPlane -ne 'cloud_primary' -or
        $watch.sync.contractId -ne 'sync-envelope-v1' -or
        $watch.sync.envelopeVersion -ne 1 -or
        $watch.sync.mode -ne 'encrypted_entity_exchange_local_staged_remote_unverified' -or
        $watch.sync.canonicalExchangeRoute -ne '/sync/v2/exchange' -or
        $watch.sync.localImplementationStatus -ne 'android_and_worker_implemented_unverified' -or
        $watch.sync.remoteVerificationStatus -ne 'missing' -or
        $watch.sync.supportsPcOff -ne $false -or
        $watch.sync.supportsBidirectionalDelta -ne $false -or
        $watch.sync.legacyIngressTargetStatus -ne 410)) {
        $issues.Add(
            'watchintervals: registry must remain local-staged/remote-unverified partial ' +
            'until verifiable remote and restart evidence exists'
        )
    }
}

$healthResults = @{}
if ($Live) {
    $urls = New-Object System.Collections.Generic.HashSet[string]
    foreach ($project in $registry.projects) {
        foreach ($url in @(
                $project.mcp.localHealth,
                $project.mcp.cloudHealth,
                $project.sync.cloudHealth
            )) {
            if ($url) { [void]$urls.Add([string]$url) }
        }
    }
    foreach ($service in $registry.supportServices) {
        if ($service.health) { [void]$urls.Add([string]$service.health) }
    }
    foreach ($url in $urls) {
        $healthResults[$url] = Test-HttpEndpoint $url
    }
}

$allRuntimeProjects = @($registry.projects | Where-Object { $_.runtimeProject })
$validManifestProjectIds = @($manifestRows | Where-Object Valid | ForEach-Object Id)
$completionEligibleProjectIds = @(
    $manifestRows | Where-Object CompletionEligible | ForEach-Object Id
)
$unverifiedCompletionClaims = @(
    $manifestRows | Where-Object {
        $_.Valid -and $_.RequiresEvidence -and -not $_.EvidenceVerified
    }
)
$evidenceClaimRows = @($manifestRows | Where-Object RequiresEvidence)
$verifiedEvidenceClaims = @($evidenceClaimRows | Where-Object EvidenceVerified)
$completeProjects = @($activeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    $_.id -in $completionEligibleProjectIds
})
$completeAllProjects = @($allRuntimeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    ($_.lifecycle -ne 'active' -or $_.id -in $completionEligibleProjectIds)
})

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# MCP 与关机同步审计')
$lines.Add('')
$lines.Add("生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$lines.Add('')
$lines.Add(
    "结论：正式运行时项目中 **$($completeAllProjects.Count)/$($allRuntimeProjects.Count)**，" +
    "当前活跃运行时项目中 **$($completeProjects.Count)/$($activeProjects.Count)** " +
    '满足各自适用门禁（完整实现或显式 local_only 豁免）。'
)
$lines.Add(
    "Canonical manifest：**$($validManifestProjectIds.Count)/$($activeProjects.Count)**；" +
    "主动证据已复核：**$($verifiedEvidenceClaims.Count)/$($evidenceClaimRows.Count)**；" +
    "合同副本缺口：**$($contractGaps.Count)**；完成证据缺口：**$($evidenceGaps.Count)**。"
)

if (@($registry.securityFindings).Count -gt 0) {
    $lines.Add('')
    $lines.Add('## 安全阻断项')
    $lines.Add('')
    foreach ($finding in $registry.securityFindings) {
        $findingPath = '`' + $finding.path + '`'
        $lines.Add(
            "- **$($finding.severity.ToUpperInvariant()) / $($finding.projectId)** " +
            "$findingPath：$($finding.finding) $($finding.requiredAction)"
        )
    }
}
$lines.Add('')
$lines.Add('| 项目 | MCP | 同步 | 关机后新增数据 | Canonical manifest | 健康检查 |')
$lines.Add('| --- | --- | --- | --- | --- | --- |')

foreach ($project in $registry.projects) {
    $manifest = @($manifestRows | Where-Object Id -eq $project.id | Select-Object -First 1)
    $manifestState = if ($project.lifecycle -eq 'archived') {
        '不适用'
    } elseif ($manifest.Count -and $manifest[0].Valid -and
        $manifest[0].RequiresEvidence -and -not $manifest[0].EvidenceVerified) {
        '结构已校验/主动证据未复核'
    } elseif ($manifest.Count -and $manifest[0].Valid) {
        '已校验'
    } elseif ($manifest.Count -and $manifest[0].Found) {
        '无效'
    } else {
        '缺失'
    }
    $pcOff = if ($project.lifecycle -eq 'archived') {
        '已封存'
    } elseif ($project.sync.status -eq 'complete' -and $manifest.Count -and
        $manifest[0].CompletionEligible -and $manifest[0].SupportsPcOff) {
        '已验证支持'
    } elseif ($project.sync.status -eq 'complete' -and $manifest.Count -and
        $manifest[0].Valid -and $manifest[0].SupportsPcOff) {
        '待主动证据复核'
    } elseif ($project.sync.status -eq 'complete') {
        '声明无效/证据缺失'
    } elseif ($project.sync.status -eq 'exempt') {
        '明确豁免'
    } else {
        '不完整'
    }
    $projectHealth = @()
    if ($Live) {
        foreach ($url in @(
                $project.mcp.localHealth,
                $project.mcp.cloudHealth,
                $project.sync.cloudHealth
            )) {
            if ($url -and $healthResults.ContainsKey($url)) {
                $projectHealth += $healthResults[$url]
            }
        }
    }
    $healthText = if (-not $Live) {
        '未执行'
    } elseif ($projectHealth.Count -eq 0) {
        '无端点'
    } elseif (@($projectHealth | Where-Object { -not $_.Ok }).Count -eq 0) {
        "通过 $($projectHealth.Count)/$($projectHealth.Count)"
    } else {
        $okCount = @($projectHealth | Where-Object Ok).Count
        "部分 $okCount/$($projectHealth.Count)"
    }
    $mcpStatus = '`' + $project.mcp.status + '`'
    $syncStatus = '`' + $project.sync.status + '`'
    $lines.Add(
        "| $(Escape-Markdown $project.name) | $mcpStatus | " +
        "$syncStatus | $pcOff | $manifestState | $healthText |"
    )
}

$lines.Add('')
$lines.Add('## 已确认的关键缺口')
$lines.Add('')
foreach ($project in $registry.projects | Where-Object {
    $_.lifecycle -eq 'active' -and -not $_.requirementsMet
}) {
    $lines.Add("- **$($project.name)**：$($project.mcp.coverage) $($project.sync.pcOffBehavior)")
}

$lines.Add('')
$lines.Add('## 策略例外')
$lines.Add('')
foreach ($exception in $registry.policyExceptions) {
    $policy = '`' + $exception.dataPolicy + '`'
    $lines.Add("- **$($exception.name)** ($policy)：$($exception.reason)")
}

$lines.Add('')
$lines.Add('## 云端支撑服务')
$lines.Add('')
$lines.Add('| 服务 | 角色 | 公开 canonical | 已部署 | 有远端备份 | 健康 |')
$lines.Add('| --- | --- | --- | --- | --- | --- |')
foreach ($service in $registry.supportServices) {
    $health = if (-not $Live) {
        '未执行'
    } elseif (-not $service.health) {
        '无探测端点'
    } elseif ($healthResults.ContainsKey($service.health) -and $healthResults[$service.health].Ok) {
        "HTTP $($healthResults[$service.health].Status)"
    } else {
        '失败'
    }
    $serviceId = '`' + $service.id + '`'
    $role = if ($service.role) { $service.role } else { 'support' }
    $canonical = if (Test-HasProperty $service 'publicCanonical') {
        $service.publicCanonical
    } else {
        '未声明'
    }
    $lines.Add(
        "| $serviceId | $(Escape-Markdown $role) | $canonical | " +
        "$($service.deployed) | $($service.remoteBackedUp) | $health |"
    )
}

$lines.Add('')
$lines.Add('## Manifest 门禁缺口')
$lines.Add('')
if ($manifestGaps.Count -eq 0) { $lines.Add('- 无') }
foreach ($gap in $manifestGaps) { $lines.Add("- $gap") }

$lines.Add('')
$lines.Add('## 协议合同一致性缺口')
$lines.Add('')
if ($contractGaps.Count -eq 0) { $lines.Add('- 无') }
foreach ($gap in $contractGaps) { $lines.Add("- $gap") }

$lines.Add('')
$lines.Add('## 能力迁移缺口')
$lines.Add('')
if ($capabilityGaps.Count -eq 0) { $lines.Add('- 无') }
foreach ($gap in $capabilityGaps) { $lines.Add("- $gap") }

$lines.Add('')
$lines.Add('## 完成状态证据缺口')
$lines.Add('')
if ($evidenceGaps.Count -eq 0) { $lines.Add('- 无') }
foreach ($gap in $evidenceGaps) { $lines.Add("- $gap") }

$lines.Add('')
$lines.Add('## 待主动复核的完成声明')
$lines.Add('')
if ($unverifiedCompletionClaims.Count -eq 0) { $lines.Add('- 无') }
foreach ($claim in $unverifiedCompletionClaims) {
    $lines.Add("- $($claim.Id): 必须使用 -VerifyEvidence 或 -Strict -Live 复核")
}

if ($issues.Count -gt 0) {
    $lines.Add('')
    $lines.Add('## 注册表或声明错误')
    $lines.Add('')
    foreach ($issue in $issues) { $lines.Add("- $issue") }
}

$resolvedReport = [System.IO.Path]::GetFullPath($ReportPath)
$reportDirectory = Split-Path -Parent $resolvedReport
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
Set-Content -LiteralPath $resolvedReport -Value ($lines -join "`r`n") -Encoding UTF8

[pscustomobject]@{
    FormalRuntimeProjects = $allRuntimeProjects.Count
    ActiveRuntimeProjects = $activeProjects.Count
    ValidManifests = $validManifestProjectIds.Count
    ManifestGaps = $manifestGaps.Count
    ContractGaps = $contractGaps.Count
    EvidenceGaps = $evidenceGaps.Count
    UnverifiedCompletionClaims = $unverifiedCompletionClaims.Count
    ActiveEvidenceChecks = $runActiveEvidenceChecks
    CapabilityGaps = $capabilityGaps.Count
    FullyCompliantProjects = $completeProjects.Count
    SecurityFindings = @($registry.securityFindings).Count
    SecurityBlockers = $securityBlockers.Count
    RegistryIssues = $issues.Count
    LiveChecks = $healthResults.Count
    LiveFailures = @($healthResults.Values | Where-Object { -not $_.Ok }).Count
    Report = $resolvedReport
} | Format-List

if ($issues.Count -gt 0) { exit 1 }
if ($ManifestStrict -and (
        $manifestGaps.Count -gt 0 -or
        $contractGaps.Count -gt 0 -or
        $evidenceGaps.Count -gt 0
    )) {
    exit 2
}
if ($VerifyEvidence -and (
        $manifestGaps.Count -gt 0 -or
        $contractGaps.Count -gt 0 -or
        $evidenceGaps.Count -gt 0 -or
        @($healthResults.Values | Where-Object { -not $_.Ok }).Count -gt 0
    )) {
    exit 2
}
if ($Strict -and (
        $manifestGaps.Count -gt 0 -or
        $contractGaps.Count -gt 0 -or
        $evidenceGaps.Count -gt 0 -or
        $unverifiedCompletionClaims.Count -gt 0 -or
        $capabilityGaps.Count -gt 0 -or
        $securityBlockers.Count -gt 0 -or
        @($healthResults.Values | Where-Object { -not $_.Ok }).Count -gt 0
    )) {
    exit 2
}
exit 0
