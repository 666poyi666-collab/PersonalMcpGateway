# Changelog

## 0.1.0-dev

- Push local Journal entries to Journal Cloud MCP with date/revision idempotent upserts, including chunked full-body reads. Treat `PHONE_OFFLINE` and `WATCH_OFFLINE` board results as neutral recovery states while preserving real execution errors as failures.
- Split snapshot ingestion from connector access: Watch/Foxlink now accept bounded,
  allowlisted pushes only at `POST /sync/push` with an independent `SYNC_KEY` Bearer;
  the MCP capability key can no longer write snapshots. Fix the watchdog cadence
  expression so `everyPasses: 1` runs every pass, and close REQ-002 after verifying
  Journal's authenticated LAN-only 8781 boundary in the active firewall and HTTP API.
- Rebuild both boards around peer project sections: each project gets a full-width
  block in its own art (FocusLink hairline instrument, 拾光 warm paper, 间歇跑 dark
  lime dial with its own dark surface in both themes), the gateway shrinks to a peer
  block plus a slim fleet strip, project-owned mcp widgets render inside their
  sections, and re-renders skip when data is unchanged. Ship the live
  board-widgets config (focus/journal/watch data cards + repo freshness).
- Add the cloud layer (REQ-001 P1-P3 partial): journal CRUD MCP plus watch/foxlink
  snapshot-mirror MCPs on Cloudflare Workers (D1, capability-path auth), and
  fleet/cloud_sync.py — driven by the watchdog every ~5 minutes — pushing local
  read-tool snapshots with honest synced/stale/never_synced + PHONE_OFFLINE skip
  semantics. CLOUD-SYNC.md delivered into each source project folder.
- Integrate the fleet into both boards: each card's probes now carry the underlying
  Windows service state (`服务已停止` / `服务未安装` vs plain `不可用`), the status bar
  shows the watchdog state, and the desktop titlebar (and its offline screen) gains a
  one-click repair button that drops a trigger file for the watchdog — no PowerShell
  window, no UAC, and it still works while the gateway itself is down. Service states
  are read via ctypes/advapi32 (BUG-005 fixed the missing restypes).
- Add `fleet/Update-PoyiFleet.ps1` for code redeploys (wheel → private Python, ordered
  service restarts under maintenance.flag, ACL re-stamp after install — BUG-004).
- Add the Cloudflare hybrid roadmap (`docs/cloud-architecture.md`, REQ-001): Workers
  remote MCP per project so ChatGPT/Claude keep working while the PC is off.
- Add the fleet reliability layer under `fleet/`: the PoyiFleetWatchdog Windows service
  health-probes all four projects (MCP + tunnel), starts stopped services, force-restarts
  degraded ones with cooldown and hourly caps, and re-applies the ProgramData ACL baseline
  at every boot; `Repair-PoyiFleet.cmd` (one-click elevated repair) and
  `Status-PoyiFleet.cmd` (no-elevation status table) ship alongside, plus
  `docs/fleet-operations.md` and `BUG_CATALOG.md` (BUG-001..003).
- Fix the dashboard default tunnel readiness ports for Watch (8880), Foxlink (8878) and
  Journal (8887), which were shuffled across the three projects (BUG-001).
- Establish the independent Personal MCP Gateway repository.
- Add the `mcp` widget type: the board calls read-only tools on each project's own loopback MCP
  (Foxlink focus totals, watch workout and sleep summaries, journal counts and recent titles) with
  curated presenters per tool; write-verb tools are refused before a connection opens, and journal
  body text never reaches the board.
- Add differentiated presentation: widgets carry `group` / `flavor` / `accent`, and each source
  project keeps its own art on the shared board — FocusLink's hairline instrument, 拾光's warm
  paper and ochre ink, 间歇跑's dark lime watch dial — with per-project integration specs under
  `docs/integrations/`.
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
