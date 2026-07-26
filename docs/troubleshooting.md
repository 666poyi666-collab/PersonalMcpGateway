# Troubleshooting

Run `service\status.ps1`, then check gateway `/readyz` and tunnel `/readyz` independently. A ready
gateway with a degraded module means that module's adapter or source application is unavailable. A
healthy gateway with an unavailable ChatGPT app normally means the tunnel, workspace association,
runtime key, or developer-mode app needs attention. Diagnose WatchIntervals through its independent
`PoyiWatchMcp` and `PoyiWatchTunnel` services, not through this Gateway.

Use `personal-mcp-gateway doctor`, official `tunnel-client doctor --explain`, and
`service\collect-support-bundle.ps1`. Bundles are redacted but must still be reviewed before public
upload. Never enable tunnel raw HTTP logging with real personal data.
