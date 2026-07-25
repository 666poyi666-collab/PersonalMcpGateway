# Windows Deployment

Run from an elevated PowerShell prompt:

```powershell
powershell -ExecutionPolicy Bypass -File service\install.ps1 -TunnelId tunnel_REPLACE_ME
```

The installer downloads pinned official WinSW and tunnel-client assets, verifies SHA-256, installs
two automatic services, encrypts entered secrets, restricts ACLs, starts the gateway, waits for
readiness, and then starts the tunnel.

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
