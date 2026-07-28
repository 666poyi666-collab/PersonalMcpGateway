# MCP 与关机同步审计

生成时间：2026-07-28 05:54:06 +08:00

结论：正式运行时项目中 **0/6**，当前活跃运行时项目中 **0/6** 满足各自适用门禁（完整实现或显式 local_only 豁免）。
Canonical manifest：**5/6**；合同副本缺口：**0**；完成证据缺口：**0**。

## 安全阻断项

- **CRITICAL / math-mistake-manager** `chatgpt-web-automation/auth.json`：The repository previously exposed a non-empty browser authentication state containing cookies and origins. It was changed to private and archived on 2026-07-27. Revoke the affected ChatGPT/browser sessions and purge the secret from Git history before any future unarchive. Changing visibility does not invalidate copied cookies or erase historical exposure.
- **CRITICAL / focuslink** `legacy public FocusLink DO /v1/* and /v2/*`：STOP-SHIP observed 2026-07-28 00:35+08: a wholly fabricated but format-valid fl2 token received HTTP 200 from GET /v1/tasks (23207 bytes) and GET /v1/live (105 bytes). No response body or real credential was read or recorded. Contain the legacy public routes with Cloudflare Access, route removal, or WAF first; then enforce real credential, scope, deviceId, and account binding on every /v1/* and /v2/* route. Prove fake=401, read-only write=403, spoofed/revoked/expired/cross-account denial, and prevent the legacy endpoint from serving as a second official origin.
- **CRITICAL / poyi-oauth-as** `unrouted Cloudflare Worker versions and secret metadata`：An unintended external write uploaded version 44908773... at 2026-07-27T16:46:45Z and a secret operation produced version c2641689... at 16:46:48Z. Secret metadata lists JWT_ACTIVE_PRIVATE_JWK; no secret value is recorded here. workers.dev /healthz remains 404 and no route is active. Retain both unrouted versions and the existing secret as staged and unusable. Do not delete, roll back, reprovision, reveal the secret, or perform any remote action. Complete local implementation and local gates first, report the evidence, and wait for separate authorization before touching Cloudflare.

| 项目 | MCP | 同步 | 关机后新增数据 | Canonical manifest | 健康检查 |
| --- | --- | --- | --- | --- | --- |
| FocusLink | `partial` | `partial` | 不完整 | 已校验 | 未执行 |
| WatchIntervals | `partial` | `partial` | 不完整 | 已校验 | 未执行 |
| SuixinYiTing | `missing` | `missing` | 不完整 | 无效 | 未执行 |
| Daylight Journal | `partial` | `partial` | 不完整 | 已校验 | 未执行 |
| PersonalMcpGateway | `complete` | `missing` | 不完整 | 已校验 | 未执行 |
| 不做手机控 | `partial` | `missing` | 不完整 | 已校验 | 未执行 |
| EchoDiary | `missing` | `missing` | 已封存 | 不适用 | 未执行 |
| VideoFlow | `missing` | `missing` | 已封存 | 不适用 | 未执行 |
| Math Mistake Manager | `missing` | `missing` | 已封存 | 不适用 | 未执行 |

## 已确认的关键缺口

- **FocusLink**：The single public foxlink-cloud-mcp origin must read the authoritative DO v2 feed through a sync-on-read derived projection and expose epoch, lag, and degraded state. It is not a second data authority. The one public origin must proxy encrypted SyncEnvelopeV1 writes to the internal authority and expose only verified metadata through MCP. Current plaintext V2 feed code is not eligible for PC-off completion.
- **WatchIntervals**：Cloud MCP exposes only encrypted-sync metadata, counts, cursors and freshness. Training, plan, route, heart and sleep plaintext remains unavailable to the cloud. Encrypted SyncEnvelopeV1 exchange, Android outbox/cursor/conflict handling, explicit tombstones, pull-first bootstrap, WorkManager recovery and Keystore-backed recovery/approval packages are implemented locally. Deployed remote exchange, Android real-device key flows, PC-off readback and restart catch-up evidence are not complete. Legacy /sync/push and plaintext V1 data routes are locally retired with 410.
- **SuixinYiTing**：No cloud MCP projection exists. Any future projection must remain metadata-only and cannot expose NetEase credentials, media URLs, audio bytes, caches, or playback diagnostics. The required encrypted state plane for queue references, favorites, progress and preferences is not implemented. NetEase cookie/token, media URL, audio bytes and cache remain excluded.
- **Daylight Journal**：Local and cloud tools use different identifiers and operations; append/status are local-only and delete is cloud-only. The encrypted V2 Worker and browser client are implemented locally, but staging OAuth, attachment object transport, real device recovery and PC-off acceptance evidence are still required.
- **PersonalMcpGateway**：All five approved runtime diagnostics tools are reachable through the authenticated tunnel while Windows is running. The tunnel is transport, not a cloud runtime or durable diagnostics store. Runtime diagnostics remain local-only. The separately scoped encrypted dashboard profile for layout, tile sizes, pinned items and preferences is not yet connected to a cloud authority.
- **不做手机控**：The app reads FocusLink focus state locally, but no verified projection exists for this product's encrypted rules, completion records or configuration. The application must reuse FocusLink account authority and device identity for encrypted rules, state, completions and configuration. It currently only reads live focus state.

## 策略例外

- **ChatGPT Branch Map** (`local_only`)：Source code explicitly states that imported ChatGPT exports remain in browser IndexedDB and are never uploaded.

## 云端支撑服务

| 服务 | 角色 | 公开 canonical | 已部署 | 有远端备份 | 健康 |
| --- | --- | --- | --- | --- | --- |
| `foxlink-cloud-mcp` | canonical_public_gateway | True | False | True | 未执行 |
| `journal-cloud-mcp` | support | 未声明 | True | True | 未执行 |
| `watch-cloud-mcp` | support | 未声明 | True | True | 未执行 |
| `focuslink-device-sync-worker` | internal_authoritative_upstream | False | True | True | 未执行 |

## Manifest 门禁缺口

- 无

## 协议合同一致性缺口

- 无

## 能力迁移缺口

- focuslink: MCP is partial
- focuslink: sync is partial
- watchintervals: MCP is partial
- watchintervals: sync is partial
- suixinyiting: MCP is missing
- suixinyiting: sync is missing
- daylight-journal: MCP is partial
- daylight-journal: sync is partial
- personal-mcp-gateway: sync is missing
- do-not-phone: MCP is partial
- do-not-phone: sync is missing

## 完成状态证据缺口

- 无

## 注册表或声明错误

- suixinyiting manifest: sync.status does not match registry (expected missing, got partial)
- sync-envelope-v1/journal-cloud-worker: schemaRelativePath differs from canonical artifact
