# ChatGPT Setup

Prerequisites are separate: the Platform organization needs Tunnel permissions and ChatGPT needs
developer mode. This account was observed to have the Tunnel creation surface and developer mode,
but runtime permissions must still be verified during deployment.

1. Create one tunnel in Platform and record its fixed Tunnel ID.
2. Create a Runtime API Key, never an Admin API Key.
3. Install the Windows services with the Tunnel ID and securely enter the key.
4. Run `tunnel\doctor.ps1` and `tunnel\verify.ps1`.
5. In ChatGPT, edit the existing personal-data developer app, select Tunnel, and choose the fixed
   Tunnel ID.
6. Refresh the tool list and call system status, Watch status, plans, and latest sleep.
7. After read/write and failure tests pass, disable the legacy Watch-only app.

Protocol success, Tunnel success, and ChatGPT account authorization are separate acceptance gates.
