# Testing

Local gate:

```powershell
uv sync --all-extras --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov --cov-report=term-missing
uv build
```

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
