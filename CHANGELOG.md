# Changelog

## 0.1.0-dev

- Establish the independent Personal MCP Gateway repository.
- Add the extensible widget area (扩展面板) to both boards: `board-widgets.yaml` declares cards
  with built-in `text`, `agenda`, `projects` (read-only git freshness), and loopback-only `remote`
  types; a broken widget renders as an error card without taking the board down.
- Default both boards to the light theme with dark one click away; the desktop window paints the
  saved theme's surface before the page loads so startup never flashes the opposite mode.
- Rewrite the README opening in plain language (总机房 + 总看板) with a three-step quick start,
  and add the `docs/board-widgets.md` widget guide.
- Add a responsive real-time control center for Personal, Watch, Foxlink, Journal, their Secure MCP
  Tunnels, 24-hour activity, recent calls, and redacted errors.
- Add loopback-only YAML dashboard targets so additional independent MCP projects can be monitored
  without changing Gateway core or the frontend.
- Add the Poyi Control Center desktop board: a frameless WebView2 window with a status tray icon,
  persisted geometry, dark and light themes, a compact panel, always-on-top, and hide-to-tray. The
  renderer makes no network calls; all gateway traffic runs in Python over the bridge.
- Encode status by shape as well as hue on the dashboard dots and the tray icon, so state survives
  colour vision deficiency at tray size.
- Add a user-scoped desktop installer, verifier, and uninstaller under `desktop/` that need no
  administrator rights, build the private runtime from `uv.lock`, and gate on a real window.
- Add shared probe caching, manual refresh, and an in-memory project recovery timeline.
- Add official MCP SDK integration, modular adapters, SQLite state, administration endpoints,
  and Windows service tooling.
- Remove the legacy WatchIntervals Adapter, tools, Resources, credential handling, and service
  coupling after WatchIntervals moved to its own independent MCP Server.
- Remove retired Watch manifest and DPAPI credential files during in-place upgrades.
