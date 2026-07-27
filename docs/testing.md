# Testing

Local gate:

```powershell
uv sync --all-extras --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov --cov-report=term-missing
uv run pip-audit
uv build
git diff --check
```

Desktop release work also runs `node --check` on `desktop.js`, parses the three PowerShell desktop
scripts, reinstalls with `desktop\install-desktop.ps1 -Autostart -Launch`, and finishes with
`desktop\Verify-PersonalMcpDesktop.cmd`. The live check must confirm that all shortcuts target
`PoyiControlCenter.exe`, the application tree contains only that GUI launcher plus WebView2, and an
in-app `PrintWindow` capture renders the full board without activating the window.

Contract tests freeze the `personal_*` management surface and each installed adapter's independent
tool namespace. They also assert that WatchIntervals tools and Resources are absent. Fault tests
cover invalid manifests, SQLite locking, process termination, and module recovery.

Release evidence must include Inspector output, Tunnel doctor output, real ChatGPT calls, Windows
restart and sleep/wake results, 1000 status calls, 100 adapter recovery cycles, 20 restarts per
service, 24-hour active testing, and 72-hour idle testing. Timed tests write checkpoints so a stopped
or sleeping test cannot be reported as passing.

Run each durable gate explicitly with `tests/soak/run-gates.ps1 -Gate <name>`. Evidence is written
under `evidence/`; active and idle gates remain `running` until the full wall-clock duration has
elapsed. The `evidence/` directory is local-only and must never be committed.

On Windows, `service\Verify-PersonalMcpGateway.cmd` runs both 20-restart gates, the redacted Tunnel
doctor, and final service account, startup mode, health, and readiness checks with one UAC prompt.
