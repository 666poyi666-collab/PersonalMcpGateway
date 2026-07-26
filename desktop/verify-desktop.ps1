# Verifies a user-scoped Poyi Control Center install and records the evidence.
#
# Runs unelevated, like the install it checks. The launch gate is the part that
# matters: imports resolving proves the runtime, but only a real window proves
# WebView2, the tray backend and the shortcut target all work together.

[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$EvidenceDir,
    [switch]$SkipLaunchCheck,
    [int]$LaunchTimeoutSeconds = 40
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'desktop-common.ps1')

if (-not ([Management.Automation.PSTypeName]'PoyiWindowProbe').Type) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class PoyiWindowProbe
{
    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")]
    private static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT placement);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    private struct WINDOWPLACEMENT
    {
        public int length;
        public int flags;
        public int showCmd;
        public POINT ptMinPosition;
        public POINT ptMaxPosition;
        public RECT rcNormalPosition;
    }

    // Returns "WxH state" for the board window owned by processId, or null.
    //
    // Sized from the restored placement rather than GetWindowRect, and matched
    // without requiring visibility: minimized and hidden-to-tray are both normal
    // states for this app, and GetWindowRect reports a minimized window as a
    // 158x26 stub parked off-screen, which would read as "no board".
    public static string Find(uint processId, string title)
    {
        string found = null;
        EnumProc callback = delegate(IntPtr hWnd, IntPtr lParam)
        {
            uint owner;
            GetWindowThreadProcessId(hWnd, out owner);
            if (owner != processId) return true;
            StringBuilder buffer = new StringBuilder(256);
            GetWindowTextW(hWnd, buffer, buffer.Capacity);
            if (buffer.ToString() != title) return true;
            WINDOWPLACEMENT placement = new WINDOWPLACEMENT();
            placement.length = Marshal.SizeOf(typeof(WINDOWPLACEMENT));
            if (!GetWindowPlacement(hWnd, ref placement)) return true;
            RECT rect = placement.rcNormalPosition;
            int width = rect.Right - rect.Left;
            int height = rect.Bottom - rect.Top;
            // The tray backend owns a message window carrying the same title at
            // roughly 237x39, so size is what separates the board from plumbing.
            if (width < 300 || height < 300) return true;
            string state = IsIconic(hWnd) ? "minimized"
                : (IsWindowVisible(hWnd) ? "visible" : "hidden-to-tray");
            found = width + "x" + height + " " + state;
            return false;
        };
        EnumWindows(callback, IntPtr.Zero);
        GC.KeepAlive(callback);
        return found;
    }
}
'@
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EvidenceDir)) {
    $EvidenceDir = Join-Path $repositoryRoot 'evidence'
}
$layout = Get-DesktopLayout -InstallDir $InstallDir
$python = $layout.Python
$resultPath = Join-Path $EvidenceDir 'desktop-verification-result.json'
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

function Test-Shortcut {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Label)

    $link = Read-DesktopShortcut -Path $Path
    if ($null -eq $link) { throw "The $Label shortcut is missing: $Path" }
    if ($link.target -ne $layout.PythonW) {
        throw "The $Label shortcut points at $($link.target) instead of $($layout.PythonW)."
    }
    if ($link.arguments -ne $layout.Arguments) {
        throw "The $Label shortcut passes '$($link.arguments)' instead of '$($layout.Arguments)'."
    }
    [ordered]@{ name = $Label; path = $Path; target = $link.target }
}

try {
    foreach ($interpreter in @($python, $layout.PythonW)) {
        if (-not (Test-Path -LiteralPath $interpreter)) {
            throw "The private runtime is incomplete: $interpreter is missing."
        }
    }

    & $python -c 'import personal_mcp_gateway.desktop.app, webview, pystray, PIL'
    if ($LASTEXITCODE -ne 0) { throw 'The desktop dependencies do not import in the runtime.' }
    # Package name via argv: Windows PowerShell strips double quotes out of native
    # arguments, so a quoted literal inside the -c source never survives the call.
    $version = & $python -c 'import importlib.metadata as m, sys; print(m.version(sys.argv[1]))' `
        personal-mcp-gateway
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the installed package version.' }
    $version = "$version".Trim()

    if (-not (Test-Path -LiteralPath $layout.Icon)) {
        throw "The shortcut icon is missing: $($layout.Icon)"
    }
    $iconHeader = [IO.File]::ReadAllBytes($layout.Icon)[0..3]
    if (($iconHeader -join ',') -ne '0,0,1,0') { throw 'The shortcut icon is not a valid .ico.' }

    $shortcuts = @(
        (Test-Shortcut -Path $layout.DesktopLink -Label 'Desktop'),
        (Test-Shortcut -Path $layout.StartMenuLink -Label 'StartMenu')
    )
    $autostart = Test-Path -LiteralPath $layout.StartupLink
    if ($autostart) { $shortcuts += (Test-Shortcut -Path $layout.StartupLink -Label 'Startup') }

    $webview2 = Get-WebView2Version
    if ($null -eq $webview2 -and -not $SkipLaunchCheck) {
        throw 'The WebView2 runtime is not installed, so the board cannot render.'
    }

    $launch = [ordered]@{ mode = 'skipped'; window = $null }
    if (-not $SkipLaunchCheck) {
        # An instance the operator already started counts: the single-instance
        # guard would refuse a second one, and killing theirs to prove a point
        # is not verification.
        $existing = @(Get-DesktopProcess -Runtime $layout.Runtime)
        if ($existing.Count -gt 0) {
            $processId = $existing[0].Id
            $launch.mode = 'attached-to-running-instance'
        } else {
            $started = Start-Process -FilePath $layout.PythonW -ArgumentList $layout.Arguments `
                -WorkingDirectory $layout.Root -PassThru
            $processId = $started.Id
            $launch.mode = 'launched-and-stopped'
        }
        try {
            $deadline = (Get-Date).AddSeconds($LaunchTimeoutSeconds)
            do {
                # Re-expanded every pass: the trampoline's child appears a moment
                # after launch and it is the one that owns the window.
                $window = $null
                foreach ($candidate in @(Expand-ProcessTree -ProcessId $processId)) {
                    $window = [PoyiWindowProbe]::Find([uint32]$candidate, $layout.AppName)
                    if ($null -ne $window) { break }
                }
                if ($null -ne $window) { break }
                # The single-instance guard is session-wide, so a copy started from
                # anywhere -- a development checkout included -- makes ours exit at
                # once. That is a different fault from a window that never paints.
                if ($launch.mode -eq 'launched-and-stopped' -and $started.HasExited) {
                    throw ("The board exited immediately (code $($started.ExitCode)). " +
                        'Another instance most likely holds the single-instance lock; ' +
                        'close it and re-run.')
                }
                Start-Sleep -Milliseconds 500
            } until ((Get-Date) -ge $deadline)
            if ($null -eq $window) {
                throw "No $($layout.AppName) window appeared within $LaunchTimeoutSeconds seconds."
            }
            $launch.window = $window
        } finally {
            if ($launch.mode -eq 'launched-and-stopped') {
                # The whole tree: stopping only the trampoline leaves the real
                # interpreter running as an orphan, still holding the tray icon.
                foreach ($candidate in @(Expand-ProcessTree -ProcessId $processId)) {
                    Stop-Process -Id $candidate -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }

    # Informational only. The board is designed to render a "no link" state when
    # the gateway is down, so an unreachable gateway is a thing it reports, not a
    # thing that makes the install wrong.
    try {
        [void](Invoke-RestMethod 'http://127.0.0.1:8761/healthz' -TimeoutSec 5)
        $gatewayReachable = $true
    } catch {
        $gatewayReachable = $false
    }

    $webView2Label = if ($null -eq $webview2) { 'missing' } else { $webview2 }
    $elevated = Test-RunningElevated
    [ordered]@{
        status           = 'passed'
        finishedAt       = [DateTimeOffset]::UtcNow.ToString('o')
        scope            = 'user'
        elevated         = $elevated
        installDir       = $layout.Root
        version          = $version
        webView2         = $webView2Label
        autostart        = $autostart
        shortcuts        = $shortcuts
        launch           = $launch
        gatewayReachable = $gatewayReachable
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host "Poyi Control Center $version verified." -ForegroundColor Green
    Write-Host "  Evidence: $resultPath"
} catch {
    [ordered]@{
        status     = 'failed'
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        installDir = $layout.Root
        error      = $_.Exception.Message
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
}
