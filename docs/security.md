# Security

- Every listener defaults to loopback. LAN application APIs use pairing authentication and stable
  device identity validation; an IP address is never identity.
- Tunnel operation uses a Runtime API Key. Admin API Keys are prohibited in services.
- Runtime and pairing secrets are encrypted with Windows DPAPI LocalMachine and protected by
  service-SID ACLs. Plaintext exists only in the child process environment.
- Admin mutations and support downloads require `X-Admin-Token`; browser-origin requests are
  restricted to loopback.
- Logs and bundles redact authorization, tokens, pairing values, content, stages, coordinates, and
  routes. Raw upstream responses are never logged.
- A public issue or test artifact must use synthetic IDs, addresses, plans, routes, and health data.

Local LAN HTTP remains a known boundary: it is suitable only for a trusted home network. Moving to
TLS or an authenticated local proxy is a separate WatchIntervals protocol migration.
