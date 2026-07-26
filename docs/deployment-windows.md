# Windows Deployment

Run the repository-owned interactive installer and approve its single UAC prompt:

```powershell
service\Install-PersonalMcpGateway.cmd
```

The installer downloads pinned official WinSW and tunnel-client assets, verifies SHA-256, installs
two automatic services, encrypts entered secrets, restricts ACLs, starts the gateway, waits for
readiness, and then starts the tunnel. Existing Tunnel credentials in ProgramData are retained on
upgrade. Installation evidence is written to `evidence/windows-install-result.json`.

Run the Windows restart and Tunnel doctor verification from the repository:

```powershell
service\Verify-PersonalMcpGateway.cmd
```

This restarts the Gateway and Tunnel 20 times each, waits for both readiness endpoints after every
restart, checks service accounts and startup modes, and saves redacted evidence under `evidence/`.
The Tunnel doctor can report `oauth_metadata` for the unauthenticated loopback MCP target; external
authentication remains the responsibility of Secure MCP Tunnel. No placeholder OAuth endpoints are
published merely to suppress that diagnostic.

Operations:

```powershell
service\status.ps1
service\restart.ps1
service\collect-support-bundle.ps1
service\uninstall.ps1
```

Uninstall retains `%ProgramData%\Poyi\PersonalMcpGateway`. Permanent removal requires
`uninstall.ps1 -PurgeData` and PowerShell confirmation. Test installation, upgrade, rollback,
reboot, no-login startup, sleep/wake, network recovery, and forced process termination before a
release.
