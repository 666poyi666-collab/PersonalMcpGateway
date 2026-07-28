# FocusLink（专注数据）接入

FocusLink 的专注账本以 Account Durable Object 为唯一 authority。Windows、手机和手表只是
产生及同步数据的设备；它们全部离线时，已经被 authority 接收并验证的数据仍可由
ChatGPT 通过 canonical cloud MCP 查询。

## PC-off 数据链路

```text
ChatGPT
  -> OAuth access token（仅 focuslink:read）
  -> foxlink-cloud-mcp /mcp（唯一公开 canonical origin）
  -> Cloudflare service binding
  -> FocusLink Account DO（唯一写 authority）
  -> 最小只读 derived projection
```

PersonalMcpGateway 是 loopback Windows 服务，不在这条 PC-off 链路中。它只负责本地
看板、开发验证和平台合同；不得因本机 Gateway 在线而宣称云 authority 在线。

严格端到端加密且所有设备离线时，云端无法解密业务数据。为了满足“所有设备离线时仍能
从云 MCP 读取专注任务、次数和时长”，FocusLink 明确采用
`sensitive_cloud_allowed` 的最小 server-readable projection。允许字段仅包括：

- session/task 标识、任务来源与标题；
- 开始/结束时间、完成/中止状态；
- active、paused、wall 时长；
- 按任务聚合、change sequence 和 authority freshness。

`note`、`tags`、设备 ID、OAuth/device credential、第三方 cookie/token、原始诊断和
不做手机控规则均不得进入该 projection。不做手机控只消费 FocusLink 已允许的专注摘要；
它自己的规则、配置和完成状态继续使用加密同步。

## 公共工具合同

中央合同是
[`focuslink-cloud-mcp-v1.schema.json`](../../standards/project-platform/contracts/focuslink-cloud-mcp-v1.schema.json)。
所有公共工具只接受 `focuslink:read`：

| Canonical 工具 | 内容 | 兼容别名 |
| --- | --- | --- |
| `focuslink_get_status` | authority、changeSeq、lastVerifiedAt、freshness | `foxlink_get_status`、`foxlink_get_sync_overview` |
| `focuslink_get_today_summary` | 今日次数与 active/paused/wall 汇总 | `foxlink_get_today_summary` |
| `focuslink_list_focus_records` | 分页专注记录与关联任务 | `foxlink_list_sessions` |
| `focuslink_get_task_summary` | 时间范围内按任务聚合的次数、时长和最近专注 | 无 |

旧 `foxlink_*` 名称只作为兼容别名，返回同一 authority envelope，不得从本地 SQLite 或
Windows 快照拼出另一份“云结果”。

每个响应都必须提供：

- `authority: "focuslink-account-do"`；
- `generatedAt`、`lastVerifiedAt`、数据覆盖截止时间 `dataThrough` 和单调 `changeSeq`；
- `freshness.state`: `fresh`、`stale` 或 `unknown`；
- `freshness.ageMs` 与固定 15 分钟的 `staleAfterMs`。

`fresh` 只表示 projection 最近成功核对过 authority，不表示手机、手表或 PC 在线。
authority 无法核对时可以返回最后的 projection，但必须标为 `stale` 或 `unknown`，不能
伪装实时。

## 鉴权与 readiness

- MCP 使用 OAuth access token，资源必须精确匹配 canonical `/mcp`，scope 必须包含
  `focuslink:read`。
- device sync token、pair nonce、内部 service credential 和旧 `foxlink:read` scope
  在 MCP 上一律拒绝。
- `/healthz` 只报告进程存活。
- `/readyz` 只有在 OAuth AS/JWKS/introspection、authority service binding 和 derived
  projection storage 均真实可用时才返回 `200`；否则返回 `503` 和经过清理的依赖名称。

当前合同与本地代码仍属 `partial`。在 canonical Worker 合同副本、staging OAuth、真实
设备同步、三轮 PC-off 读取证据和部署 revision 同时匹配前，禁止设置
`supportsPcOff=true` 或 `complete`。
