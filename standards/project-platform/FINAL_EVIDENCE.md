# Final release evidence

The `verification` object in `.poyi/project-platform.json` is the release-evidence
manifest used to authorize `sync.status: complete` and `supportsPcOff: true`.
The audit fails closed: missing, malformed, stale, or mismatched evidence keeps
the project incomplete.

An MCP-only remote capability uses the common source/deployment, test-command,
and remote-probe evidence. Physical ADB and three-round PC-off evidence are
required only when `sync.status` is `complete`; they are not filler fields for a
read-only MCP release.

## Required evidence

`sourceDeployments` binds every source named by the central registry to the exact
deployed version:

- Git sources provide a reachable commit, its Git tree hash and the corresponding
  `git-tree-sha1` or `git-tree-sha256` algorithm.
- Non-Git sources use `sha256-path-content-v1`. The audit hashes only the explicit
  registry allow-list as sorted `path`, byte length, and file SHA-256 records.
  Runtime/build folders and secret-bearing filenames are excluded.
- `deployedVersion` must be the immutable provider/app version. Values such as
  `pending`, `latest`, `unknown`, or `n/a` are rejected.
- Every source-to-deployment mapping references a hashed evidence file.

`remoteProbes` must exactly include the method, URL, and expected status declared
by the registry. URLs must use the approved HTTPS origin and cannot contain user
info, query strings, or fragments. `-VerifyEvidence` replays these secret-free
GET/HEAD probes.

`adbDevices` records only a role (`phone`, `tablet`, or `watch`), physical-device
and emulator assertions, package/app version, APK SHA-256, a salted device
attestation SHA-256, the matching client source-deployment id/tree hash, and an
evidence reference. Attestation hashes must be unique across roles. Never store
an ADB serial, LAN IP, build fingerprint, account identifier, pairing URL, or
credential.

`pcOffRounds` contains exactly three rounds numbered 1-3. Across those rounds:

- mutation kinds are exactly `create`, `update`, and `delete`;
- the PC is either shut down or every local participant is stopped;
- the cloud revision changes and is not reused across rounds;
- remote MCP reads the change while the PC is unavailable;
- restart catch-up and exactly-once behavior pass; and
- the delete round proves a tombstone.

Every device, deployment, probe, and PC-off round references a repository-relative
JSON evidence summary with a matching file SHA-256. The summary has only seven
fields: schema version, id, kind, `pass`, capture time, producer test-command id,
and a SHA-256 binding to the exact declared claim. Raw logs, responses, device
properties, and credentials are forbidden. Each producer command must be named by
the central acceptance policy and is rerun by `-VerifyEvidence`.

`-ManifestStrict` validates structure and hashes but never labels a complete claim
as verified. Only `-VerifyEvidence` (or `-Strict -Live`) reruns the producer commands
and secret-free remote probes; until then the report says `待主动证据复核`.

`releaseSetSha256` is lowercase SHA-256 over UTF-8 of the sorted source deployment
records, one per line with a final newline. Each record is
`id|kind|commit-or-empty|tree-algorithm|lowercase-tree-hash|deployed-version|deployed-at`.
The claim SHA-256 is lowercase SHA-256 over UTF-8 without a trailing newline:

| Kind | Claim text |
| --- | --- |
| source deployment | `source\|id\|kind\|commit-or-empty\|tree-algorithm\|lowercase-tree-hash\|deployed-version\|deployed-at\|release-set-sha256` |
| remote probe | `probe\|id\|method\|url\|status\|source-deployment-id\|deployed-version\|release-set-sha256` |
| ADB device | `adb\|role\|package\|app-version\|lowercase-artifact-sha256\|lowercase-attestation-sha256\|release-set-sha256` |
| PC-off round | `pc-off\|round\|mutation\|device-role\|isolation-mode\|before-cursor\|after-cursor\|started-at\|finished-at\|release-set-sha256` |
| ChatGPT MCP | `chatgpt\|app-id\|app-version-id\|mcp-url\|True\|comma-separated-sorted-tools\|release-set-sha256` |

PC-off revisions use the strict lowercase `c<base36>` cursor. Each round advances
the cursor, the next round starts at the previous round's result, timestamps are
ordered, and the three evidence summaries are distinct.

## FocusLink release set

FocusLink cannot be marked complete until the evidence covers all of the following:

- source/deployment mappings for `focuslink-client`, `foxlink-cloud-mcp`, and the
  non-Git `poyi-oauth-as` source tree;
- a `chatgptMcp` record whose App ID, App Version ID, canonical MCP URL, connected
  OAuth state, and four canonical tool names exactly match the central registry;
- `/healthz`, `/readyz`, OAuth protected-resource metadata, and the authenticated
  `/sync/v2/status` challenge on the canonical public origin;
- physical phone, tablet, and watch ADB roles;
- a sanitized `chatgpt-oauth-mcp` proof; and
- three PC-off rounds covering create, update, and delete.

Run the structural gate while work is in progress:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-projects.ps1 `
  -ManifestStrict -ReportPath "$env:TEMP\project-platform-audit.md"
```

Before release, replay tests and remote probes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-projects.ps1 `
  -Live -Strict -VerifyEvidence -ReportPath .\reports\audit-latest.md
```
