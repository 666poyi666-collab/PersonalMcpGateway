# WatchIntervals Extraction

WatchIntervals is intentionally not a Personal MCP Gateway module. Its API client, schemas, error
mapping, tools, Resources, secrets, and lifecycle were moved into the independent MCP Server in the
WatchIntervals repository.

The resulting runtime boundary is:

```text
ChatGPT -> Watch Secure MCP Tunnel -> PoyiWatchMcp -> WatchIntervals phone API
```

Personal MCP Gateway does not register `watch_*` tools, publish `watch://` Resources, read a Watch
token, discover the phone, or connect to the watch. The two products use separate ports, services,
Tunnel IDs, Runtime Keys, data directories, logs, and ChatGPT applications. Stopping or upgrading
one must not affect the other.
