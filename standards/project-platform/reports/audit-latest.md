# MCP 与关机同步审计

生成时间：2026-07-27 21:42:18 +08:00

结论：正式产品中 **0/6**，当前活跃运行时项目中 **0/6** 同时满足远程 MCP 全覆盖与电脑关机后继续同步。

## 安全阻断项

- **CRITICAL / math-mistake-manager** `chatgpt-web-automation/auth.json`：The repository previously exposed a non-empty browser authentication state containing cookies and origins. It was changed to private and archived on 2026-07-27. Revoke the affected ChatGPT/browser sessions and purge the secret from Git history before any future unarchive. Changing visibility does not invalidate copied cookies or erase historical exposure.

| 项目 | MCP | 同步 | 关机后新增数据 | 项目清单 | 健康检查 |
| --- | --- | --- | --- | --- | --- |
| FocusLink | `partial` | `partial` | 不完整 | 待落地 | 通过 3/3 |
| WatchIntervals | `partial` | `partial` | 不完整 | 待落地 | 通过 2/2 |
| SuixinYiTing | `missing` | `missing` | 不完整 | 待落地 | 无端点 |
| Daylight Journal | `partial` | `partial` | 不完整 | 待落地 | 通过 2/2 |
| PersonalMcpGateway | `partial` | `missing` | 不完整 | 待落地 | 通过 1/1 |
| 不做手机控 | `missing` | `partial` | 不完整 | 待落地 | 无端点 |
| EchoDiary | `missing` | `missing` | 已封存 | 不适用 | 无端点 |
| VideoFlow | `missing` | `missing` | 已封存 | 不适用 | 无端点 |
| Math Mistake Manager | `missing` | `missing` | 已封存 | 不适用 | 无端点 |

## 已确认的关键缺口

- **FocusLink**：Cloud exposes three no-argument snapshots plus sync overview; current session, one-session detail, resources, complete pagination, and commands are absent. The MCP mirror retains the last Windows snapshot. The installed desktop device-sync endpoint is loopback, so new data reaching the MCP while Windows is off is not proven.
- **WatchIntervals**：Cloud exposes six summary snapshots plus sync overview; plan detail/groups, workout detail, route/heart resources, sync state, and commands are absent. Phone-side push code exists, but remote D1 contained only pc-sync rows during this audit. PC-independent operation is not deployed or verified.
- **SuixinYiTing**：No persistent MCP data plane. Playback cache, session, and application state are device-local.
- **Daylight Journal**：Local and cloud tools use different identifiers and operations; append/status are local-only and delete is cloud-only. ChatGPT can create cloud entries while Windows is off, but cloud-created rows are not pulled into the local journal. Local mobile changes require the local sync API or wait for Windows.
- **PersonalMcpGateway**：Five diagnostics tools are reachable through a Windows-dependent secure tunnel only. Gateway diagnostics and fleet state disappear when Windows is off.
- **不做手机控**：No MCP server; local supervision rules and statistics are not exposed to ChatGPT. The app can read FocusLink live state directly from its cloud endpoint, but its own rules and statistics do not synchronize bidirectionally.

## 策略例外

- **ChatGPT Branch Map** (`local_only`)：Source code explicitly states that imported ChatGPT exports remain in browser IndexedDB and are never uploaded.

## 云端支撑服务

| 服务 | 已部署 | 有远端备份 | 健康 |
| --- | --- | --- | --- |
| `foxlink-cloud-mcp` | True | True | HTTP 200 |
| `journal-cloud-mcp` | True | True | HTTP 200 |
| `watch-cloud-mcp` | True | True | HTTP 200 |
| `focuslink-device-sync-worker` | True | True | HTTP 200 |

## 未登记候选

- `C:\开发\小工具\UsbVolumeCurve`：Executable project marker found but absent from the formal project inventory.
- `C:\开发\小工具\B站下载脚本`：Standalone local Git repository absent from the formal project inventory.
- `C:\开发\工具服务\ComfyUI代理工具`：Standalone local Git repository absent from the formal project inventory.
- `C:\开发\实验项目\AI文档实验`：Standalone local Git repository; classify as product or experiment.
- `C:\开发\学习资料\数学试卷分析`：Standalone local Git repository; classify as product or data workspace.
- `C:\开发\个人anki系统`：Idea/workspace exists but currently contains no implementation or Git repository.

## 标准采用缺口

- focuslink: missing .poyi/project-platform.json
- focuslink: MCP is partial
- focuslink: sync is partial
- watchintervals: missing .poyi/project-platform.json
- watchintervals: MCP is partial
- watchintervals: sync is partial
- suixinyiting: missing .poyi/project-platform.json
- suixinyiting: MCP is missing
- suixinyiting: sync is missing
- daylight-journal: missing .poyi/project-platform.json
- daylight-journal: MCP is partial
- daylight-journal: sync is partial
- personal-mcp-gateway: missing .poyi/project-platform.json
- personal-mcp-gateway: MCP is partial
- personal-mcp-gateway: sync is missing
- do-not-phone: missing .poyi/project-platform.json
- do-not-phone: MCP is missing
- do-not-phone: sync is partial
