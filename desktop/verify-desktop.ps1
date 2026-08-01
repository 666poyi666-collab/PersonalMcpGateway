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
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class PoyiWindowProbe
{
    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool IsZoomed(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")]
    private static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT placement);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int index);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    private static extern IntPtr SendMessageW(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);
    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(
        IntPtr hWnd, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")]
    private static extern void mouse_event(
        uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);

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

    public static string ResizeContract(uint processId, string title)
    {
        // PowerShell is DPI-unaware by default. WM_NCHITTEST and the cursor use
        // physical screen coordinates, so virtualized rectangles can probe the
        // wrong pixels on a scaled display while still looking plausible.
        IntPtr previousDpi = SetThreadDpiAwarenessContext(new IntPtr(-4));
        try
        {
            return ResizeContractDpiAware(processId, title);
        }
        finally
        {
            if (previousDpi != IntPtr.Zero)
                SetThreadDpiAwarenessContext(previousDpi);
        }
    }

    private static string ResizeContractDpiAware(uint processId, string title)
    {
        IntPtr found = IntPtr.Zero;
        EnumProc callback = delegate(IntPtr hWnd, IntPtr lParam)
        {
            uint owner;
            GetWindowThreadProcessId(hWnd, out owner);
            if (owner != processId) return true;
            StringBuilder buffer = new StringBuilder(256);
            GetWindowTextW(hWnd, buffer, buffer.Capacity);
            if (buffer.ToString() != title) return true;
            RECT rect;
            if (!GetWindowRect(hWnd, out rect)) return true;
            if (rect.Right - rect.Left < 300 || rect.Bottom - rect.Top < 300) return true;
            found = hWnd;
            return false;
        };
        EnumWindows(callback, IntPtr.Zero);
        GC.KeepAlive(callback);
        if (found == IntPtr.Zero) return "failed:no-board-window";

        const long WS_THICKFRAME = 0x00040000;
        const long WS_EX_TOOLWINDOW = 0x00000080;
        const long WS_EX_APPWINDOW = 0x00040000;
        const long WS_EX_NOACTIVATE = 0x08000000;
        long style = GetWindowLongPtr(found, -16).ToInt64();
        if ((style & WS_THICKFRAME) == 0) return "failed:no-thickframe";
        long exStyle = GetWindowLongPtr(found, -20).ToInt64();
        bool desktopWidget =
            (exStyle & WS_EX_TOOLWINDOW) != 0 &&
            (exStyle & WS_EX_NOACTIVATE) != 0 &&
            (exStyle & WS_EX_APPWINDOW) == 0;
        RECT windowRect;
        RECT clientRect;
        GetWindowRect(found, out windowRect);
        GetClientRect(found, out clientRect);
        int middleY = (windowRect.Top + windowRect.Bottom) / 2;
        long rightPoint = ((long)(middleY & 0xffff) << 16) | (uint)((windowRect.Right - 2) & 0xffff);
        long cornerPoint = ((long)((windowRect.Bottom - 2) & 0xffff) << 16) |
            (uint)((windowRect.Right - 2) & 0xffff);
        long right = SendMessageW(found, 0x0084, IntPtr.Zero, new IntPtr(rightPoint)).ToInt64();
        long corner = SendMessageW(found, 0x0084, IntPtr.Zero, new IntPtr(cornerPoint)).ToInt64();
        if (desktopWidget)
        {
            if (right != 1 || corner != 1)
                return "failed:desktop-hit=" + right + "/" + corner;
        }
        else if (right != 11 || corner != 17)
        {
            return "failed:hit=" + right + "/" + corner;
        }
        int frameX = (windowRect.Right - windowRect.Left) - (clientRect.Right - clientRect.Left);
        int frameY = (windowRect.Bottom - windowRect.Top) - (clientRect.Bottom - clientRect.Top);
        if (Math.Abs(frameX) > 1 || Math.Abs(frameY) > 1)
            return "failed:visible-frame=" + frameX + "x" + frameY;

        string live = desktopWidget ? "skipped-desktop-widget" : "skipped-window-state";
        if (!desktopWidget && IsWindowVisible(found) && !IsIconic(found) && !IsZoomed(found))
        {
            live = DragBottomRight(found, windowRect);
            if (live.StartsWith("failed:")) return live;
        }
        if (desktopWidget)
            return "passed:desktop-widget hit=1/1 frame=" + frameX + "x" + frameY +
                " live=" + live;
        return "passed:right=11 corner=17 frame=" + frameX + "x" + frameY + " live=" + live;
    }

    private static string DragBottomRight(IntPtr hWnd, RECT original)
    {
        POINT cursor;
        if (!GetCursorPos(out cursor)) return "failed:cursor-unavailable";
        int width = original.Right - original.Left;
        int height = original.Bottom - original.Top;
        int startX = original.Right - 7;
        int startY = original.Bottom - 7;
        const uint LEFT_DOWN = 0x0002;
        const uint LEFT_UP = 0x0004;
        const uint RESTORE_FLAGS = 0x0014; // SWP_NOZORDER | SWP_NOACTIVATE

        try
        {
            BringWindowToTop(hWnd);
            SetForegroundWindow(hWnd);
            Thread.Sleep(180);
            if (GetForegroundWindow() != hWnd)
            {
                // Foreground-lock rules can reject SetForegroundWindow for a
                // verifier process. A plain titlebar tap activates the board
                // without invoking any command or changing its rectangle.
                SetCursorPos(original.Left + (width / 2), original.Top + 30);
                mouse_event(LEFT_DOWN, 0, 0, 0, UIntPtr.Zero);
                Thread.Sleep(30);
                mouse_event(LEFT_UP, 0, 0, 0, UIntPtr.Zero);
                Thread.Sleep(180);
            }
            SetCursorPos(startX, startY);
            Thread.Sleep(120);
            mouse_event(LEFT_DOWN, 0, 0, 0, UIntPtr.Zero);
            Thread.Sleep(120);
            HashSet<string> transitionFrames = new HashSet<string>();
            // Native sizing follows pointer movement 1:1. Move progressively so
            // this probe measures live resize frames instead of teleporting the
            // cursor once and mistaking the immediate response for a missing
            // transition.
            for (int frame = 1; frame <= 8; frame++)
            {
                SetCursorPos(
                    startX - ((48 * frame) / 8),
                    startY - ((32 * frame) / 8));
                Thread.Sleep(18);
                RECT sample;
                if (GetWindowRect(hWnd, out sample))
                {
                    transitionFrames.Add(
                        (sample.Right - sample.Left) + "x" + (sample.Bottom - sample.Top));
                }
            }
            mouse_event(LEFT_UP, 0, 0, 0, UIntPtr.Zero);
            Thread.Sleep(360);

            RECT changed;
            if (!GetWindowRect(hWnd, out changed)) return "failed:post-drag-rect";
            int changedWidth = changed.Right - changed.Left;
            int changedHeight = changed.Bottom - changed.Top;
            int deltaWidth = changedWidth - width;
            int deltaHeight = changedHeight - height;
            if (deltaWidth > -12 || deltaHeight > -12 || deltaWidth < -96 || deltaHeight < -80)
                return "failed:live-drag=" + width + "x" + height + "->" +
                    changedWidth + "x" + changedHeight;
            if (transitionFrames.Count < 4)
                return "failed:live-transition-frames=" + transitionFrames.Count;
            return width + "x" + height + "->" + changedWidth + "x" + changedHeight +
                " frames=" + transitionFrames.Count;
        }
        finally
        {
            mouse_event(LEFT_UP, 0, 0, 0, UIntPtr.Zero);
            SetWindowPos(
                hWnd, IntPtr.Zero, original.Left, original.Top, width, height, RESTORE_FLAGS);
            SetCursorPos(cursor.X, cursor.Y);
        }
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
$desktopMode = $false
if (Test-Path -LiteralPath $layout.State) {
    $savedState = Get-Content -LiteralPath $layout.State -Raw | ConvertFrom-Json
    $desktopModeProperty = $savedState.PSObject.Properties['desktop_mode']
    if ($null -ne $desktopModeProperty) {
        $desktopMode = [bool]$desktopModeProperty.Value
    }
}

function Test-Shortcut {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Arguments
    )

    $link = Read-DesktopShortcut -Path $Path
    if ($null -eq $link) { throw "The $Label shortcut is missing: $Path" }
    if ($link.target -ne $layout.Launcher) {
        throw "The $Label shortcut points at $($link.target) instead of $($layout.Launcher)."
    }
    if ($link.arguments -ne $Arguments) {
        throw "The $Label shortcut passes '$($link.arguments)' instead of '$Arguments'."
    }
    [ordered]@{ name = $Label; path = $Path; target = $link.target }
}

try {
    foreach ($interpreter in @($python, $layout.PythonW, $layout.Launcher)) {
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
        (Test-Shortcut -Path $layout.DesktopLink -Label 'Desktop' -Arguments $layout.Arguments),
        (Test-Shortcut -Path $layout.StartMenuLink -Label 'StartMenu' -Arguments $layout.Arguments)
    )
    $autostart = Test-Path -LiteralPath $layout.StartupLink
    if ($autostart) {
        $shortcuts += (Test-Shortcut -Path $layout.StartupLink -Label 'Startup' `
                -Arguments $layout.StartupArguments)
    }

    $webview2 = Get-WebView2Version
    if ($null -eq $webview2 -and -not $SkipLaunchCheck) {
        throw 'The WebView2 runtime is not installed, so the board cannot render.'
    }

    $launch = [ordered]@{ mode = 'skipped'; window = $null; resize = $null }
    if (-not $SkipLaunchCheck) {
        # An instance the operator already started counts: the single-instance
        # guard would refuse a second one, and killing theirs to prove a point
        # is not verification.
        $existing = @(Get-DesktopProcess -Runtime $layout.Runtime)
        if ($existing.Count -gt 0) {
            $processId = $existing[0].Id
            $launch.mode = 'attached-to-running-instance'
        } else {
            $started = Start-Process -FilePath $layout.Launcher -ArgumentList $layout.Arguments `
                -WorkingDirectory $layout.Root -WindowStyle Hidden -PassThru
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
                    if ($null -ne $window) {
                        $windowProcessId = $candidate
                        break
                    }
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
            if ($desktopMode -and -not $window.EndsWith(' hidden-to-tray')) {
                throw ('Desktop mode exposed the management canvas instead of keeping it hidden: ' +
                    $window)
            }
            if ($desktopMode) {
                # The management form starts hidden in card mode, so its
                # before_show resize hook intentionally has not run yet.
                $launch.resize = 'skipped:hidden-management'
            } else {
                $launch.resize = [PoyiWindowProbe]::ResizeContract(
                    [uint32]$windowProcessId, $layout.AppName)
                if (-not $launch.resize.StartsWith('passed:')) {
                    throw "The frameless resize contract failed: $($launch.resize)."
                }
            }
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
