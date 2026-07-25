# Migration From WatchIntervals

The legacy `WatchIntervals/mcp` implementation is a migration source only. Personal MCP Gateway
replaces its hand-written JSON-RPC HTTP surface and scheduled-task watchdog with the official SDK,
module boundaries, SQLite state, WinSW services, and one fixed tunnel.

WatchIntervals remains responsible for Watch/phone applications, training state, plan and workout
storage, route storage, system sleep access, LAN APIs, and phone-to-watch synchronization. Its phone
API protocol v2 accepts revision-protected idempotent plan writes while preserving legacy clients.

Do not remove legacy startup tasks or ChatGPT apps until the new gateway passes real E2E and reboot
tests. After cutover, disable old tasks, retain rollback instructions, and remove legacy tunnel
lifecycle code in a separate WatchIntervals commit.
