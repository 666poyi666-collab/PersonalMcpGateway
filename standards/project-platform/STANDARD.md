# MCP and Cloud Sync Standard

Version: 1.0 (2026-07-27)

## 1. Meaning of the two requirements

### Remote MCP

"ChatGPT can call all data" means every product data class approved for AI use
is present in a data inventory and is reachable through a remote MCP tool or
resource. It does not mean sending credentials, raw private logs, signing keys,
or unrestricted local files to ChatGPT.

Use tools for bounded queries and commands. Use resources with pagination for
long text, histories, routes, media metadata, and other large data. Every
omitted data class must have an explicit reason in the project manifest.

### PC-independent sync

"Sync while the PC is off" means a phone, watch, browser, or other client can
exchange changes with a cloud data plane directly. A cloud server that only
retains the last snapshot pushed by Windows is remote availability, but it is
not continued synchronization.

The cloud service must never claim an offline device is online. Commands that
require hardware return `device_offline`, `lastSyncAt`, and an expiry-aware
queued state where supported.

Dashboard and operational APIs use the following data-plane terms without
weakening that definition:

- `cloud_primary`: a durable cloud database remains available while Windows is
  off; this alone does not prove device synchronization or later reconciliation;
- `snapshot_mirror`: the cloud retains the last successful Windows/device
  snapshot; this is remote read availability, not continued synchronization;
- `local_only`: the runtime or data requires the Windows PC.

Power-off read availability, write availability, and continued synchronization
must be represented separately. A UI must not derive one from another.

## 2. Canonical routes

New implementations use these exact routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Process liveness; sanitized and unauthenticated |
| `GET` | `/readyz` | Storage/binding readiness; sanitized |
| `POST` | `/mcp` | MCP Streamable HTTP endpoint |
| `GET` | `/.well-known/oauth-protected-resource/mcp` | MCP OAuth resource metadata |
| `POST` | `/sync/v1/exchange` | Bidirectional delta exchange |
| `GET` | `/sync/v1/status` | Authenticated device sync status |

Current `/<ACCESS_KEY>/mcp` and `/sync/push` routes are legacy migration
routes. They may remain during compatibility windows, but new projects must
not copy them as the final design. Capability keys in URLs leak into logs and
cannot provide client-scoped revocation.

Local development uses the same route names on loopback:

```text
http://127.0.0.1:<allocated-port>/mcp
http://127.0.0.1:<allocated-port>/healthz
http://127.0.0.1:<allocated-port>/readyz
```

The cloud hostname is project-specific; route names and payload contracts are
not.

## 3. Authentication boundaries

- MCP clients use OAuth 2.1 Authorization Code + PKCE with narrow scopes.
- Devices use independently issued, revocable device credentials.
- MCP credentials and device sync credentials are never interchangeable.
- APKs and desktop packages do not contain owner/admin secrets.
- Secrets are never stored in Git, manifests, reports, URLs, screenshots, or
  support bundles.
- Write tools require a stable `requestId`; device commands also require
  `commandId`, `expectedRevision`, `expectedState`, and `expiresAt`.

## 4. Synchronization contract

`POST /sync/v1/exchange` is the only required sync operation. A request carries
the caller's cursor and an outbox batch; the response acknowledges mutations
and returns remote changes.

Minimum logical envelope:

```json
{
  "protocolVersion": 1,
  "deviceId": "opaque-device-id",
  "cursor": "opaque-cursor-or-null",
  "mutations": [
    {
      "opId": "stable-idempotency-id",
      "entityType": "session",
      "entityId": "opaque-entity-id",
      "baseRevision": 7,
      "operation": "upsert",
      "payload": {}
    }
  ]
}
```

Minimum response fields are `acknowledged`, `changes`, `nextCursor`, and
`serverTime`.

Required behavior:

- durable client outbox before network I/O;
- idempotent mutation replay by `opId`;
- monotonic per-entity revision or an explicitly documented CRDT policy;
- tombstones for deletions;
- bounded pages and resumable cursors;
- exponential retry with jitter;
- conflict responses that preserve both versions until resolved;
- cloud-to-device pull as well as device-to-cloud push;
- `lastSyncAt`, `lastSuccessfulPushAt`, and `lastSuccessfulPullAt` diagnostics;
- schema/version negotiation and backward-readable migrations.

Large binary artifacts belong in object storage with integrity hashes and
short-lived scoped access. They do not belong in MCP JSON or D1 rows.

## 5. Repository declaration

Every active runtime project must contain:

```text
.poyi/project-platform.json
```

The file declares data policy, MCP coverage, sync mode, canonical routes,
health endpoints, and known gaps. Source code stays in each stack's native
layout; this standard deliberately does not force Java, Python, TypeScript,
and .NET into one directory structure.

Projects that intentionally never upload data use `dataPolicy: local_only`
and `status: exempt`, with the product reason written in the manifest. Drafts,
archives, research folders, and third-party clones are not runtime projects.

## 6. Acceptance gates

A project is `complete` only when all applicable gates pass:

1. Cloud `/healthz` and `/readyz` return sanitized success.
2. Remote MCP initialize and `tools/list` work from outside the LAN.
3. The data inventory has 100% implemented-or-explicitly-exempt coverage.
4. MCP resources paginate complete histories without truncation.
5. With Windows shut down, a non-PC client creates or changes data and the
   cloud revision advances.
6. ChatGPT reads that new revision through remote MCP while Windows is off.
7. When Windows returns, it pulls the cloud change without overwriting a newer
   local revision.
8. Replaying the same mutation does not duplicate data.
9. Deletion propagates as a tombstone and does not resurrect on another client.
10. Device-offline commands expose honest state and expire safely.
11. Contract, migration, auth, redaction, and power-off end-to-end tests pass.

An online Worker plus a recent Windows snapshot satisfies gates 1-2 only. It
does not satisfy gates 5-7.

## 7. Migration order

1. Fix Journal bidirectional synchronization and align local/cloud tool
   identity first; it currently has two authorities.
2. Connect Watch phone-originated sync operationally and prove a `phone`
   source while Windows is off.
3. Connect FocusLink's production device-sync data to its cloud MCP instead of
   relying on the Windows snapshot mirror.
4. Move Personal Gateway diagnostics to a small cloud control-plane MCP.
5. Add scoped MCP and sync adapters to SuixinYiTing and other active products
   according to their data inventories. EchoDiary, VideoFlow, and Math are
   archived and excluded unless they are explicitly restored.
