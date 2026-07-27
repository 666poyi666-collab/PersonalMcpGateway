# Poyi Project Platform Standard

This directory is the source of truth for the two capabilities expected from
runtime projects:

1. ChatGPT can reach the project's approved data through a remote MCP server.
2. Devices synchronize through a cloud data plane that does not depend on the
   Windows PC being powered on.

The current estate is recorded in `projects.json`. The detailed protocol and
acceptance gates are in `STANDARD.md`.

## Commands

Run the structural and live endpoint audit:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-projects.ps1 -Live
```

Write the Markdown report explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-projects.ps1 `
  -Live -ReportPath .\reports\audit-latest.md
```

Use `-Strict` in CI or before declaring the migration complete. Strict mode
returns a non-zero exit code while any active runtime project is `partial` or
`missing` for MCP or PC-independent sync.

Use the independent manifest/contract gate while implementation work is still
in progress:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-projects.ps1 `
  -ManifestStrict -ReportPath "$env:TEMP\project-platform-audit.md"
```

This scans the six explicit `manifestRepositoryPath` roots, validates registry
and manifest policy agreement, and checks that registered protocol-contract
copies are byte-identical. It deliberately does not turn `partial` capabilities
into failures. `-RequireManifests` is an alias for `-ManifestStrict`.

Completed cloud capabilities must also carry commit/deploy/evidence, structured
test-command, and remote-probe declarations. Run their active checks with
`-VerifyEvidence`; `-Strict -Live` also runs them. A manifest cannot promote
itself to `complete` merely by setting `supportsPcOff: true`.

Scaffold the required per-project declaration for a new repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\new-project-manifest.ps1 `
  -ProjectRoot C:\开发\项目\Example -Id example -Name Example
```

The manifest is written to `.poyi/project-platform.json`. It contains no
credentials. Add the project to `projects.json` in the same change.

Journal's single canonical exchange schema and fixtures live in `contracts/`.
The registered Worker and application copies must match those files byte for
byte; incompatible draft envelopes are not supported in parallel.

## Scope

`C:\开发\README.md` and the repositories owned by
`666poyi666-collab` define the formal project estate. Local experiments and
privacy-preserving offline tools are discovered separately so they cannot be
silently mistaken for fully integrated products.

An explicit `local_only` exemption is valid for products whose core promise is
that data never leaves the device. Exemption is not the same as secretly
uploading a reduced copy.
