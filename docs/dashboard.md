# Real-time Dashboard

Open `http://127.0.0.1:8761/admin/status` to view the local control center. The page refreshes every
four seconds and shows the independently deployed Personal Gateway, Watch MCP, Foxlink MCP, and
Journal MCP, including each MCP readiness endpoint and Secure MCP Tunnel.

The dashboard also reports 24-hour Gateway tool activity, recent calls, recent redacted errors,
Gateway uptime, success rate, and an in-memory timeline when a project degrades, goes offline, or
recovers. The refresh button bypasses the three-second shared probe cache. The cache prevents
multiple open dashboard tabs from multiplying health traffic. The page loads no CDN scripts, fonts,
analytics, or remote resources.

The same data is available as a native window with a status tray icon. See
[Desktop board](desktop.md).

## Cloud and power-off status contract

Each entry in `targets` includes a `sync` object. It deliberately keeps three different claims
separate; a green local MCP probe is not evidence that data keeps changing after Windows shuts
down.

| `dataPlane` | What the card may claim | Current example |
| --- | --- | --- |
| `cloud_primary` | The cloud database remains readable/writable without this PC. Check `pcOff.continuedSync` separately before claiming device-to-cloud synchronization. | Journal |
| `snapshot_mirror` | The cloud can serve the last successful snapshots. It must not be labelled continued synchronization. | Watch, FocusLink |
| `local_only` | The data/runtime disappears when this PC is off. | Personal Gateway diagnostics |

`pcOff.readAvailable`, `writeAvailable`, and `continuedSync` are independent booleans.
`localDependency` is `uplink` when Windows is still needed to send local changes, `runtime` when
the whole capability needs Windows, and `none` only after a non-PC data path is verified.

The watchdog writes `cloud-sync-status.json` into the Gateway data directory. It is explicitly a
PC-side mirror: the API exposes its sanitized values under `sync.observation`: `lastAttemptAt`, `lastSuccessfulPushAt`,
`lastCompletePushAt`, pushed/skipped counts, and per-tool timestamps. `snapshotState` is derived
conservatively as `fresh`, `stale`, `incomplete`, `never_synced`, or `unknown`. These fields report
the local sync agent's last observed push; they are not a live Cloudflare health probe.

The visible sync badge has a stricter five-state contract: `fresh`, `stale`, `offline`, `blocked`,
or `unknown`. It always shows the last authority verification time, pending count, and a blocker
when one exists. A `cloud_primary` product becomes `fresh` only when the Gateway fetches a status
document from its configured HTTPS authority and validates an Ed25519 signature against the target's
pinned public key. A PC-side mirror, a healthy local MCP process, or an unsigned local JSON file
never upgrades that state. Expired, malformed, mismatched, or invalidly signed records are visibly
stale, blocked, or unknown rather than treated as a live fact. Local MCP/tunnel availability remains
a separate card signal, so a Windows process failure cannot erase a verified remote fact or pretend
to be one.

The signed authority document contains only status metadata: `schemaVersion`, `productId`,
`issuedAt`, `expiresAt`, a `product-authority` observation, and an unpadded base64url Ed25519
signature. Its signature covers the canonical JSON representation of every field except `signature`.
It must never include sync credentials, encrypted content, device identifiers, journal text, health
data, cookies, or diagnostics payloads.

The page opens in the light theme by default; the topbar toggle switches to dark and the choice
persists in the browser. Below the built-in panels sits the extensible widget area (扩展面板) —
schedules, project freshness, notes, or any local data source, declared in `board-widgets.yaml`
with no restart. See [Board widgets](board-widgets.md).

Use the topbar density control for `全量`、`紧凑`、`极小` views. The minimal view hides only secondary
facts; each project name, its sync state, and the primary metric remain visible.

## Encrypted profile

The local dashboard bundle stores only presentation preferences: card order, pinned projects, each
card's compact/standard/expanded size, theme, and information density. Its browser-local
`dashboard-profile.js` uses a non-extractable AES-256-GCM key in IndexedDB and writes the encrypted
entity, a `SyncEnvelopeV1`-shaped outbox mutation, and cursor/flight metadata in one transaction.
The mutation binds product, entity, revision, and operation into AES-GCM AAD. It contains no runtime
diagnostics, token, cookie, OAuth credential, media URL, media cache, journal text, or health data.

The bundle intentionally does not post to the network or contain a sync credential. It exposes a
local `prepareExchange`/`applyExchange` boundary for a future paired device client, and it preserves
outbox data until an acknowledged response and materialized cursor succeed together. Until a
separate encrypted cloud authority, device enrollment, recovery flow, staging proof, and PC-off
acceptance exist, this is local encrypted durability only: the project platform manifest remains
`sync.status: missing` and `supportsPcOff: false`.

## Add a target

Copy `dashboard/targets.example.yaml` to:

```text
%ProgramData%\Poyi\PersonalMcpGateway\dashboard-targets.yaml
```

Add a target with `health_url`, `ready_url`, and optionally `tunnel_ready_url`. Only loopback HTTP
and HTTPS endpoints are accepted. A target with the same ID overrides a built-in target; setting
`enabled: false` hides it. Changes appear on the next refresh and do not require a Gateway restart.
New targets may declare the same `sync` capability fields shown in `targets.example.yaml`; omit the
block when the capability has not been audited instead of guessing from local health.

For a `cloud_primary` target that has a real authority status endpoint, add `authority_status` with
an HTTPS URL and its pinned 32-byte Ed25519 public key encoded as unpadded base64url. This optional
configuration is public verification metadata, not a device token or OAuth credential. Do not add it
until the authority is staged and independently verified.

Supported icon names are `gateway`, `watch`, `link`, `journal`, and `service`. The `accent` field
accepts a CSS color used only for the target card.
