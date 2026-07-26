# Real-time Dashboard

Open `http://127.0.0.1:8761/admin/status` to view the local control center. The page refreshes every
four seconds and shows the independently deployed Personal Gateway, Watch MCP, Foxlink MCP, and
Journal MCP, including each MCP readiness endpoint and Secure MCP Tunnel.

The dashboard also reports 24-hour Gateway tool activity, recent calls, recent redacted errors,
Gateway uptime, success rate, and an in-memory timeline when a project degrades, goes offline, or
recovers. The refresh button bypasses the three-second shared probe cache. The cache prevents
multiple open dashboard tabs from multiplying health traffic. The page loads no CDN scripts, fonts,
analytics, or remote resources.

The same data is available as a native window with a status tray icon. See
[Desktop board](desktop.md).

The page opens in the light theme by default; the topbar toggle switches to dark and the choice
persists in the browser. Below the built-in panels sits the extensible widget area (扩展面板) —
schedules, project freshness, notes, or any local data source, declared in `board-widgets.yaml`
with no restart. See [Board widgets](board-widgets.md).

## Add a target

Copy `dashboard/targets.example.yaml` to:

```text
%ProgramData%\Poyi\PersonalMcpGateway\dashboard-targets.yaml
```

Add a target with `health_url`, `ready_url`, and optionally `tunnel_ready_url`. Only loopback HTTP
and HTTPS endpoints are accepted. A target with the same ID overrides a built-in target; setting
`enabled: false` hides it. Changes appear on the next refresh and do not require a Gateway restart.

Supported icon names are `gateway`, `watch`, `link`, `journal`, and `service`. The `accent` field
accepts a CSS color used only for the target card.
