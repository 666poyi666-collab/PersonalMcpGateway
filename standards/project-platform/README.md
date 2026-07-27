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

Scaffold the required per-project declaration for a new repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\new-project-manifest.ps1 `
  -ProjectRoot C:\开发\项目\Example -Id example -Name Example
```

The manifest is written to `.poyi/project-platform.json`. It contains no
credentials. Add the project to `projects.json` in the same change.

## Scope

`C:\开发\README.md` and the repositories owned by
`666poyi666-collab` define the formal project estate. Local experiments and
privacy-preserving offline tools are discovered separately so they cannot be
silently mistaken for fully integrated products.

An explicit `local_only` exemption is valid for products whose core promise is
that data never leaves the device. Exemption is not the same as secretly
uploading a reduced copy.
