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
- `derived_projection`: a non-authoritative query model is refreshed from an
  explicitly named authority; it must expose its source epoch, lag, and degraded
  state and must never accept independent writes;
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
| `POST` | `/sync/v2/exchange` | `SyncEnvelopeV1` encrypted bidirectional delta exchange |
| `GET` | `/sync/v2/status` | Authenticated encrypted device sync status |
| `POST` | `/sync/v1/pair/offers` | Owner-authenticated one-time device-pair offer |
| `POST` | `/sync/v1/pair/exchange` | Exchange a one-time pair nonce for device identity |

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

### FocusLink public-origin rule

FocusLink has exactly one public canonical origin: `foxlink-cloud-mcp`. That
origin serves `/sync/v2/exchange`, `/sync/v2/status`, `/mcp`, `/readyz`, and
OAuth protected-resource metadata. Its exchange adapter transparently forwards
writes to the internal
FocusLink Account Durable Object `/v2/sync` authority. The direct Worker/DO is an
implementation dependency, not a second official MCP, `cloudBaseUrl`, or
canonical route, and it must not appear in the project manifest. Its legacy
custom domain may remain network-reachable during migration; that reachability
must be reported honestly and does not make it canonical. Production closure
requires a service binding, equivalent access restriction, or retirement of the
legacy public domain after the adapter is proven.

The 2026-07-28 emergency containment disabled `workers_dev`, cleared routes,
and removed the exact legacy custom-domain binding. Anonymous and fabricated
token probes now stop at the edge (`530` on the retired domain and `404` on the
workers.dev bypass, 17-byte responses). This is `edge_contained_noncanonical`,
not proof that the DO is private or that application authorization is fixed.
`authorizeV2`, service binding, pairing, and canonical/negative E2E remain
release blockers.

Any foxlink D1 model used for MCP reads is a `derived_projection`, refreshed
from the DO on read and reporting `epoch`, `lag`, and `degraded`. All writes pass
through to the DO. Legacy snapshot `/sync/push` and snapshot-write routes return
`410 Gone` with the canonical base URL and migration guidance; public
`/v1/live`, task, or projection-write endpoints are forbidden. Device sync
credentials are accepted only by exchange and its upstream; MCP uses a separate
OAuth token. End-to-end proof uses the same public origin for exchange write and
MCP read, then verifies that the internal DO revision advanced.

The public read contract is `focuslink-cloud-mcp-v1`. Canonical tools are
`focuslink_get_status`, `focuslink_get_today_summary`,
`focuslink_list_focus_records`, and `focuslink_get_task_summary`; the existing
`foxlink_*` names remain compatibility aliases only. Every response reports the
Account DO authority, `changeSeq`, `lastVerifiedAt`, `dataThrough`, and
`fresh` / `stale` / `unknown` freshness. Freshness describes authority
verification, never device presence.

The approved PC-off query requirement permits ChatGPT to read
already-synchronized focus counts, task titles and durations while every
personal device is offline. That is incompatible with strict end-to-end
encryption of those fields, so FocusLink's `sensitive_cloud_allowed` policy
explicitly permits a minimal server-readable derived projection containing
session/task identifiers, task source/title, timestamps, status and duration
aggregates. Notes, tags, device identifiers, credentials, cookies, diagnostics
and Focus Guard rules remain excluded. The platform must not describe this
projection as end-to-end encrypted.

FocusLink pairing never introduces OAuth scopes. The authorization server
advertises only the four canonical scopes; `focuslink:pair` and
`devices:manage` are permanently forbidden. `/sync/v1/pair/offers` accepts only
an internal Cloudflare service-binding call caused by the AS owner-session +
CSRF flow. Its service credential is bound to audience and action, is not an
OAuth access token, is never returned to the browser, and is rejected by `/mcp`
and normal sync. Public OAuth or device tokens receive `403`/`404` on the offer
route. `/sync/v1/pair/exchange` is public only when enabled and accepts a
high-entropy one-time nonce plus allowlisted device metadata, with rate limiting,
atomic consume, expiry/replay rejection, and a server-assigned device id; it
accepts neither Bearer credentials nor a caller-selected device id. Both pairing
routes remain closed until AS binding and joint E2E pass.

## 3. Authentication boundaries

- MCP clients use OAuth 2.1 Authorization Code + PKCE with narrow scopes.
- Devices use independently issued, revocable device credentials.
- MCP credentials and device sync credentials are never interchangeable.
- APKs and desktop packages do not contain owner/admin secrets.
- Secrets are never stored in Git, manifests, reports, URLs, screenshots, or
  support bundles.
- Random 256-bit owner login tickets, authorization codes, and refresh tokens
  are stored as domain-separated HMAC-SHA-256 fingerprints with a secret pepper
  (or an explicitly justified SHA-256 fingerprint). PBKDF2 and Argon are for
  low-entropy human passwords, not high-entropy tokens. Comparison remains
  constant-time and authorization codes are consumed exactly once atomically.
- The owner trust root is the Cloudflare account plus an already authenticated
  local Wrangler session. `scripts/issue-owner-code.mjs` generates 32 random
  bytes locally and uses a Wrangler D1 command to insert only a domain-separated
  fingerprint, `user_id: poyi-owner`, issue time, and a five-minute expiry. The
  plaintext is printed once to stdout and is never stored in source, environment
  variables, or log files. No owner password/PBKDF2 path, DO issuance RPC, or
  test-only issuance backdoor may coexist.
- `owner_login_codes` are consumed by one atomic D1 update guarded by
  `consumed_at IS NULL` and `expires_at > now`, returning the user id. Owner
  login is POST-only and creates a 15-minute `__Host-oauth_session` cookie with
  Secure, HttpOnly, SameSite=Lax, and Path=/ plus CSRF, rotation, logout, and DO
  rate limiting. The rate limiter cannot issue codes. OAuth grants, authorization
  codes, refresh tokens, and access-token state remain authoritative in the
  SQLite Durable Object, separate from owner-login-code D1 state.
- Write tools require a stable `requestId`; device commands also require
  `commandId`, `expectedRevision`, `expectedState`, and `expiresAt`.

OAuth documentation uses the accurate description “OAuth 2.1 draft-15 with an
RFC 9700, RFC 7636, RFC 8707, RFC 8414, RFC 9728, and RFC 9068 profile”; it must
not claim final OAuth 2.1 RFC conformance. Deployment evidence includes a real
Cloudflare login → authorize → code exchange smoke with wall time, Worker CPU,
and confirmation that no `1102` limit error occurred. Miniflare alone is not a
deployment gate.

The authorization-server Worker uses a released compatibility date no later
than `2026-07-27`. `src/index.ts` actually exports `OAuthState`; production,
test, and Wrangler dry-run configurations bind that exact Durable Object class.
Canonical metadata advertises exactly `journal:read`, `journal:write`,
`focuslink:read`, and `watch:read`; `watch:write`, `foxlink:read`, `openid`, and
`id_token` are forbidden. Unless OIDC is deliberately implemented and tested,
`/.well-known/openid-configuration` remains `404`, OAuth discovery remains
`200`, and the service does not describe itself as an OIDC provider.

Until the authorization server and resource metadata are deployed, protected
business routes and `/readyz` fail closed with `503`; a local implementation or
Miniflare success is not remote deployment. The remote OAuth gate requires:

- authorization-server `/healthz`, RFC 8414 metadata, and JWKS return `200`;
- OpenID discovery may return `404` only when OIDC is intentionally unsupported
  and RFC 8414 metadata does not advertise it;
- Journal, FocusLink, and Watch protected-resource metadata return `200`, name
  their exact canonical `/mcp` resource, and list precisely the configured
  issuer in `authorization_servers`;
- authorization-server resource policy and each protected-resource document
  agree in both directions;
- remote valid, expired, wrong-audience, wrong-scope, and revoked-token probes
  produce their expected allow/deny outcomes.

## 4. Synchronization contract

`POST /sync/v2/exchange` is the only required sync operation. It implements
`SyncEnvelopeV1`: a request carries the caller's strict `c<base36>` cursor and
an encrypted outbox batch; the response acknowledges mutations and returns
encrypted remote changes.

Minimum logical envelope:

```json
{
  "protocolVersion": 2,
  "envelopeVersion": 1,
  "product": "journal",
  "deviceId": "opaque-device-id",
  "cursor": "c2z-or-null",
  "mutations": [
    {
      "opId": "stable-idempotency-id",
      "entityType": "journal_entry",
      "entityId": "opaque-entity-id",
      "baseRevision": 7,
      "operation": "upsert",
      "keyVersion": 1,
      "ciphertext": "base64url-aes-gcm-ciphertext",
      "nonce": "base64url-12-byte-nonce",
      "aadHash": "sha256-hex",
      "objects": []
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

### SyncEnvelopeV1

All encrypted products consume these byte-identical artifacts:

- [`contracts/sync-envelope-v1.schema.json`](contracts/sync-envelope-v1.schema.json)
- [`contracts/sync-envelope-v1.request.fixture.json`](contracts/sync-envelope-v1.request.fixture.json)
- [`contracts/sync-envelope-v1.response.fixture.json`](contracts/sync-envelope-v1.response.fixture.json)

Mutation and change operation ids are UUIDs. Each client atomically writes its
local entity, outbox and cursor/flight state before I/O; it deletes an outbox
item only after the returned changes materialize and its acknowledgement commits
in the same local persistence boundary. AES-256-GCM AAD binds product, entity,
revision, operation and key version. Clouds hold ciphertext, nonce, object
manifest metadata, tombstones and cursors only. Plaintext business `payload`
fields, third-party credentials, cookies, media bytes, raw diagnostics and
unwrapped keys are prohibited.

## 5. Repository declaration

Every active runtime project must contain:

```text
.poyi/project-platform.json
```

The file declares data policy, MCP coverage, sync mode, canonical routes,
health endpoints, and known gaps. Source code stays in each stack's native
layout; this standard deliberately does not force Java, Python, TypeScript,
and .NET into one directory structure.

The central registry names one `manifestRepositoryPath` for each active runtime
project. That canonical implementation root is mandatory. A generated publish
mirror cannot hide a missing implementation manifest; SuixinYiTing therefore
uses its development repository, not the derived publication repository, as
its manifest root.

Products whose owned state intentionally has no Poyi cloud data plane use
`dataPolicy: local_only` and both MCP/sync `status: exempt`, with the product
reason written in the manifest. This does not mean the application is offline:
an upstream media API/CDN or a read-only dependency may still be used, but it
does not convert local credentials, rules, history, cache, or statistics into a
Poyi synchronization plane. Drafts, archives, research folders, and third-party
clones are not runtime projects.

PersonalMcpGateway keeps project-level `operational_metadata`, but its
`runtime-diagnostics` inventory is `local_only`. Its authenticated tunnel can
transport live results only while Windows and the Gateway are running. A future
cloud control plane may retain no more than sanitized `last-heartbeat` and
`audit` records. Such records are historical observations with timestamps,
expiry, stale, and offline semantics; they are not live diagnostics. With the
PC off, the Gateway cannot create or advance heartbeat, runtime revision,
module health, fleet state, or any other Gateway status.

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
12. The release evidence binds every required source tree hash to a concrete
    deployment version, hashes all sanitized artifacts, matches the registered
    remote probes, records the required physical ADB roles, and contains exactly
    three ordered PC-off rounds covering create, update, and delete.

An online Worker plus a recent Windows snapshot satisfies gates 1-2 only. It
does not satisfy gates 5-7.

`sync.status: complete` and `supportsPcOff: true` are a coupled, evidence-backed
claim. Either value without a valid release-evidence manifest is a registry
error, and reports must display the capability as unverified rather than
supported. Device serials, IP addresses, OAuth codes, bearer tokens, pair nonces,
cookies, and secret material are forbidden in evidence; use role names and
SHA-256 attestations instead.

## 7. Migration order

1. Fix Journal bidirectional synchronization against the single canonical v1
   schema and align local/cloud tool identity.
2. Connect Watch phone-originated sync operationally and prove a `phone`
   source while Windows is off.
3. Make `foxlink-cloud-mcp` the only FocusLink public origin, adapt canonical
   exchange to the internal DO `/v2/sync` authority, and retire legacy snapshot
   writes with `410 Gone`.
4. Add an encrypted Personal Gateway dashboard-profile entity for layout,
   density, pinned projects and non-sensitive preferences; keep runtime
   diagnostics `local_only` and never turn historical heartbeat/audit records
   into claims of live PC state.
5. Give SuixinYiTing a minimal encrypted state plane for playback preferences,
   queue references, favorites/progress and configuration while excluding
   NetEase cookies/tokens, media URLs, audio bytes and caches. Let 不做手机控
   reuse FocusLink's account/device authority for encrypted rules, completion
   state and configuration; it must not create a second cloud authority or
   restore vendor services. EchoDiary, VideoFlow, and Math remain archived
   unless explicitly restored.
