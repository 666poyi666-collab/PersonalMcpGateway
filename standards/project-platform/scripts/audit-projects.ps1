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

function Test-HttpEndpoint([string]$Url) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
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

function Get-HttpStatus([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
        return [int]$response.StatusCode
    } catch {
        if ($null -ne $_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
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
        $profileItem[0].mcpExposure -ne 'none') {
        Add-ManifestIssue $IssueList $ProjectId (
            'dashboard-profile inventory must exist exactly once as cloud_primary with no MCP exposure'
        )
    }
    if ($Manifest.sync.status -ne 'missing' -or $Manifest.sync.authority -ne 'cloud' -or
        $Manifest.sync.supportsPcOff -ne $false -or
        $Manifest.sync.supportsBidirectionalDelta -ne $false) {
        Add-ManifestIssue $IssueList $ProjectId (
            'Gateway dashboard profile sync must remain missing/cloud with both PC-off capabilities false'
        )
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
    if ($ManifestRaw -match 'focuslink:pair' -or $ManifestRaw -match 'devices:manage') {
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
    if (-not $requiresEvidence) { return }

    $verification = $Manifest.verification
    if ($null -eq $verification) {
        $GapList.Add("${projectId}: complete status has no verification declaration")
        return
    }
    foreach ($field in @(
            'implementationCommit', 'deployedRevision', 'evidenceFiles',
            'testCommands', 'remoteProbes'
        )) {
        if (-not (Test-HasProperty $verification $field)) {
            $GapList.Add("${projectId}: verification is missing $field")
        }
    }

    $commit = [string]$verification.implementationCommit
    if ($commit -notmatch '^[0-9a-fA-F]{7,40}$') {
        $GapList.Add("${projectId}: invalid implementationCommit")
    } else {
        & git -C $Project.manifestRepositoryPath cat-file -e "$commit`^{commit}" 2>$null
        if ($LASTEXITCODE -ne 0) {
            $GapList.Add("${projectId}: implementationCommit is not present in the canonical repo")
        } else {
            & git -C $Project.manifestRepositoryPath merge-base --is-ancestor $commit HEAD 2>$null
            if ($LASTEXITCODE -ne 0) {
                $GapList.Add("${projectId}: implementationCommit is not contained by current HEAD")
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace([string]$verification.deployedRevision)) {
        $GapList.Add("${projectId}: deployedRevision must be non-empty")
    }

    $root = [System.IO.Path]::GetFullPath([string]$Project.manifestRepositoryPath).TrimEnd('\')
    $evidenceIds = @()
    foreach ($evidence in @($verification.evidenceFiles)) {
        $evidenceIds += [string]$evidence.id
        if ([System.IO.Path]::IsPathRooted([string]$evidence.path)) {
            $GapList.Add("$projectId/$($evidence.id): evidence path must be repository-relative")
            continue
        }
        $evidencePath = [System.IO.Path]::GetFullPath((Join-Path $root $evidence.path))
        if (-not $evidencePath.StartsWith(
                $root + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            $GapList.Add("$projectId/$($evidence.id): evidence path escapes the repository")
            continue
        }
        if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
            $GapList.Add("$projectId/$($evidence.id): evidence file is missing")
            continue
        }
        $actualHash = (Get-FileHash -LiteralPath $evidencePath -Algorithm SHA256).Hash
        if ($actualHash -ne ([string]$evidence.sha256).ToUpperInvariant()) {
            $GapList.Add("$projectId/$($evidence.id): evidence SHA256 mismatch")
        }
    }
    if ($projectId -eq 'watchintervals') {
        foreach ($requiredId in @('remote-exchange', 'pc-off-e2e', 'restart-catchup')) {
            if ($requiredId -notin $evidenceIds) {
                $GapList.Add("${projectId}: completion evidence is missing $requiredId")
            }
        }
    }

    if (-not $RunActiveChecks) { return }

    foreach ($command in @($verification.testCommands)) {
        $executable = [string]$command.executable
        if (-not $executable) {
            $GapList.Add("$projectId/$($command.id): test executable is empty")
            continue
        }
        Push-Location $root
        try {
            & $executable @($command.arguments) 2>&1 | Out-Null
            $actualExitCode = $LASTEXITCODE
            if ($null -eq $actualExitCode) { $actualExitCode = 0 }
        } catch {
            $actualExitCode = -1
        } finally {
            Pop-Location
        }
        if ($actualExitCode -ne [int]$command.expectedExitCode) {
            $GapList.Add(
                "$projectId/$($command.id): test exited $actualExitCode, expected " +
                "$($command.expectedExitCode)"
            )
        }
    }

    foreach ($probe in @($verification.remoteProbes)) {
        $actualStatus = Get-HttpStatus ([string]$probe.url)
        if ($actualStatus -ne [int]$probe.expectedStatus) {
            $GapList.Add(
                "$projectId/$($probe.id): remote probe returned $actualStatus, expected " +
                "$($probe.expectedStatus)"
            )
        }
    }
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
            $remote = (& git -C $path remote get-url origin 2>$null | Select-Object -First 1)
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
                    $manifest = $manifestRaw | ConvertFrom-Json
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
                    ) $project ([bool]($VerifyEvidence -or ($Strict -and $Live))) (
                        $issues
                    ) $evidenceGaps
                    $manifestValid = (
                        $issues.Count -eq $issueCountBefore -and
                        $evidenceGaps.Count -eq $evidenceGapCountBefore
                    )
                } catch {
                    $issues.Add("$($project.id) manifest: invalid JSON at $manifestPath")
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
    $focusContractComplete = (
        $focus.mcp.status -eq 'complete' -and $focus.sync.status -eq 'complete'
    )
    if ($foxlinkService.Count -ne 1 -or
        $foxlinkService[0].role -ne 'canonical_public_gateway' -or
        $foxlinkService[0].publicCanonical -ne $true -or
        $foxlinkService[0].registrationTarget -ne $true -or
        $foxlinkService[0].registeredInManifest -ne $focusManifestValid -or
        $foxlinkService[0].canonicalContractDeployed -ne $focusContractComplete -or
        $foxlinkService[0].deployed -ne $focusContractComplete -or
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
$completeProjects = @($activeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    $_.id -in $validManifestProjectIds
})
$completeAllProjects = @($allRuntimeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    ($_.lifecycle -ne 'active' -or $_.id -in $validManifestProjectIds)
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
    } elseif ($manifest.Count -and $manifest[0].Valid) {
        '已校验'
    } elseif ($manifest.Count -and $manifest[0].Found) {
        '无效'
    } else {
        '缺失'
    }
    $pcOff = if ($project.lifecycle -eq 'archived') {
        '已封存'
    } elseif ($project.sync.status -eq 'complete') {
        '支持'
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
if ($Strict -and (
        $manifestGaps.Count -gt 0 -or
        $contractGaps.Count -gt 0 -or
        $evidenceGaps.Count -gt 0 -or
        $capabilityGaps.Count -gt 0 -or
        $securityBlockers.Count -gt 0 -or
        @($healthResults.Values | Where-Object { -not $_.Ok }).Count -gt 0
    )) {
    exit 2
}
exit 0
