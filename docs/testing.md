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

Contract tests use synthetic phone/watch responses for status, plans, history, sleep, offline,
authentication, revision conflict, sync pending, and sync verified. Fault tests cover invalid JSON,
timeouts, mDNS misses, wrong identities, SQLite locking, process termination, and network recovery.

Release evidence must include Inspector output, Tunnel doctor output, real ChatGPT calls, Windows
restart and sleep/wake results, 1000 status calls, 100 adapter recovery cycles, 20 restarts per
service, 24-hour active testing, and 72-hour idle testing. Timed tests write checkpoints so a stopped
or sleeping test cannot be reported as passing.

Run each durable gate explicitly with `tests/soak/run-gates.ps1 -Gate <name>`. Evidence is written
under `evidence/`; active and idle gates remain `running` until the full wall-clock duration has
elapsed. The `evidence/` directory is local-only and must never be committed.
