[CmdletBinding()]
param(
    [string]$RegistryPath = '',
    [switch]$Live,
    [switch]$Strict,
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

function Escape-Markdown([object]$Value) {
    if ($null -eq $Value) { return '' }
    return ([string]$Value).Replace('|', '\|').Replace("`r", ' ').Replace("`n", ' ')
}

$resolvedRegistry = (Resolve-Path -LiteralPath $RegistryPath).Path
$registry = Get-Content -LiteralPath $resolvedRegistry -Raw -Encoding UTF8 | ConvertFrom-Json
$issues = New-Object System.Collections.Generic.List[string]
$complianceGaps = New-Object System.Collections.Generic.List[string]

if ($registry.standardVersion -ne 1) {
    $issues.Add("Unsupported registry standardVersion: $($registry.standardVersion)")
}

$expectedRoutes = @{
    mcp = '/mcp'
    health = '/healthz'
    ready = '/readyz'
    oauthResourceMetadata = '/.well-known/oauth-protected-resource/mcp'
    syncExchange = '/sync/v1/exchange'
    syncStatus = '/sync/v1/status'
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

    $manifestFound = $false
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            $issues.Add("$($project.id): repository path is missing: $path")
            continue
        }

        $manifestPath = Join-Path $path $project.manifestRelativePath
        if (Test-Path -LiteralPath $manifestPath) { $manifestFound = $true }

        $gitDirectory = Join-Path $path '.git'
        if (Test-Path -LiteralPath $gitDirectory) {
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

    if ($project.lifecycle -eq 'active' -and -not $manifestFound) {
        $complianceGaps.Add("$($project.id): missing .poyi/project-platform.json")
    }
    if ($project.runtimeProject -and $project.lifecycle -eq 'active') {
        if ($project.mcp.status -notin @('complete', 'exempt')) {
            $complianceGaps.Add("$($project.id): MCP is $($project.mcp.status)")
        }
        if ($project.sync.status -notin @('complete', 'exempt')) {
            $complianceGaps.Add("$($project.id): sync is $($project.sync.status)")
        }
    }

    $manifestRows += [pscustomobject]@{
        Id = $project.id
        Found = $manifestFound
    }
}

$healthResults = @{}
if ($Live) {
    $urls = New-Object System.Collections.Generic.HashSet[string]
    foreach ($project in $registry.projects) {
        foreach ($url in @($project.mcp.localHealth, $project.mcp.cloudHealth, $project.sync.cloudHealth)) {
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

$activeProjects = @($registry.projects | Where-Object {
    $_.runtimeProject -and $_.lifecycle -eq 'active'
})
$allRuntimeProjects = @($registry.projects | Where-Object { $_.runtimeProject })
$manifestProjectIds = @($manifestRows | Where-Object Found | ForEach-Object Id)
$completeProjects = @($activeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    $_.id -in $manifestProjectIds
})
$completeAllProjects = @($allRuntimeProjects | Where-Object {
    $_.mcp.status -in @('complete', 'exempt') -and
    $_.sync.status -in @('complete', 'exempt') -and
    $_.id -in $manifestProjectIds
})

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# MCP 与关机同步审计')
$lines.Add('')
$lines.Add("生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$lines.Add('')
$lines.Add(
    "结论：正式产品中 **$($completeAllProjects.Count)/$($allRuntimeProjects.Count)**，" +
    "当前活跃运行时项目中 **$($completeProjects.Count)/$($activeProjects.Count)** " +
    '同时满足远程 MCP 全覆盖与电脑关机后继续同步。'
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
$lines.Add('| 项目 | MCP | 同步 | 关机后新增数据 | 项目清单 | 健康检查 |')
$lines.Add('| --- | --- | --- | --- | --- | --- |')

foreach ($project in $registry.projects) {
    $manifest = @($manifestRows | Where-Object Id -eq $project.id | Select-Object -First 1)
    $manifestState = if ($manifest.Count -and $manifest[0].Found) { '已落地' } else { '待落地' }
    $pcOff = if ($project.sync.status -eq 'complete') {
        '支持'
    } elseif ($project.sync.status -eq 'exempt') {
        '明确豁免'
    } else {
        '不完整'
    }
    $projectHealth = @()
    if ($Live) {
        foreach ($url in @($project.mcp.localHealth, $project.mcp.cloudHealth, $project.sync.cloudHealth)) {
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
foreach ($project in $registry.projects | Where-Object { -not $_.requirementsMet }) {
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
$lines.Add('| 服务 | 已部署 | 有远端备份 | 健康 |')
$lines.Add('| --- | --- | --- | --- |')
foreach ($service in $registry.supportServices) {
    $health = if (-not $Live) {
        '未执行'
    } elseif ($healthResults.ContainsKey($service.health) -and $healthResults[$service.health].Ok) {
        "HTTP $($healthResults[$service.health].Status)"
    } else {
        '失败'
    }
    $serviceId = '`' + $service.id + '`'
    $lines.Add(
        "| $serviceId | $($service.deployed) | $($service.remoteBackedUp) | $health |"
    )
}

$lines.Add('')
$lines.Add('## 未登记候选')
$lines.Add('')
foreach ($candidate in $registry.unregisteredCandidates) {
    $candidatePath = '`' + $candidate.path + '`'
    $lines.Add("- $candidatePath：$($candidate.reason)")
}

$lines.Add('')
$lines.Add('## 标准采用缺口')
$lines.Add('')
foreach ($gap in $complianceGaps) { $lines.Add("- $gap") }

if ($issues.Count -gt 0) {
    $lines.Add('')
    $lines.Add('## 注册表错误')
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
    FullyCompliantProjects = $completeProjects.Count
    ComplianceGaps = $complianceGaps.Count
    SecurityFindings = @($registry.securityFindings).Count
    RegistryIssues = $issues.Count
    LiveChecks = $healthResults.Count
    LiveFailures = @($healthResults.Values | Where-Object { -not $_.Ok }).Count
    Report = $resolvedReport
} | Format-List

if ($issues.Count -gt 0) { exit 1 }
if ($Strict -and ($complianceGaps.Count -gt 0 -or
        @($healthResults.Values | Where-Object { -not $_.Ok }).Count -gt 0)) {
    exit 2
}
exit 0
