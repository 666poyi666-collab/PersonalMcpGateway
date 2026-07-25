# Repository Agent Instructions

- Use Python 3.12, `uv`, and the locked dependency set.
- Keep MCP SDK-specific imports and compatibility code inside `mcp/sdk_compat.py` or the MCP
  protocol boundary. Business modules must depend on public gateway types.
- Never commit secrets, device IDs, tunnel IDs, IP addresses, workout routes, private health data,
  runtime logs, support bundles, or soak evidence.
- Preserve stable tool names and structured result envelopes. Tool additions or removals require a
  Gateway restart and contract-test updates.
- Run Ruff, Pyright strict, pytest with coverage, dependency audit, build, and `git diff --check`
  before release work.
- Never use, invoke, delegate to, install, reinstall, or recommend Kimi, Kimi Code, K3,
  `kimi-agent`, or `kimi-delegate`.
