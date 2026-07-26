# Security

- Every Gateway listener defaults to loopback. Application adapters must authenticate their source
  APIs and validate stable identity; an IP address is never identity.
- Tunnel operation uses a Runtime API Key. Admin API Keys are prohibited in services.
- Runtime and application secrets are encrypted with Windows DPAPI LocalMachine and protected by
  service-SID ACLs. Plaintext exists only in the child process environment.
- Admin mutations and support downloads require `X-Admin-Token`; browser-origin requests are
  restricted to loopback.
- Logs and bundles redact authorization, tokens, pairing values, content, stages, coordinates, and
  routes. Raw upstream responses are never logged.
- A public issue or test artifact must use synthetic IDs, addresses, plans, routes, and health data.

Application LAN protocols are owned and documented by their source projects. The Gateway must not
reuse credentials belonging to an independent project such as WatchIntervals.
