# MCP 与关机同步审计

生成时间：2026-07-27 20:34:43 +08:00

结论：正式产品中 **0/8**，当前活跃运行时项目中 **0/7** 同时满足远程 MCP 全覆盖与电脑关机后继续同步。

## 安全阻断项

- **CRITICAL / math-mistake-manager** `chatgpt-web-automation/auth.json`：The public repository tracks a non-empty browser authentication state containing cookies and origins. Revoke the affected ChatGPT/browser sessions, remove the file from the current branch, add an ignore rule, and purge the secret from Git history. A normal delete commit alone does not remove historical exposure.

| 项目 | MCP | 同步 | 关机后新增数据 | 项目清单 | 健康检查 |
| --- | --- | --- | --- | --- | --- |
| FocusLink | `partial` | `partial` | 不完整 | 待落地 | 通过 3/3 |
| WatchIntervals | `partial` | `partial` | 不完整 | 待落地 | 通过 2/2 |
| SuixinYiTing | `missing` | `missing` | 不完整 | 待落地 | 无端点 |
| Daylight Journal | `partial` | `partial` | 不完整 | 待落地 | 通过 2/2 |
| PersonalMcpGateway | `partial` | `missing` | 不完整 | 待落地 | 通过 1/1 |
| EchoDiary | `missing` | `missing` | 不完整 | 待落地 | 无端点 |
| VideoFlow | `missing` | `missing` | 不完整 | 待落地 | 无端点 |
| Math Mistake Manager | `missing` | `missing` | 不完整 | 待落地 | 无端点 |

## 已确认的关键缺口

- **FocusLink**：Cloud exposes three no-argument snapshots plus sync overview; current session, one-session detail, resources, complete pagination, and commands are absent. The MCP mirror retains the last Windows snapshot. The installed desktop device-sync endpoint is loopback, so new data reaching the MCP while Windows is off is not proven.
- **WatchIntervals**：Cloud exposes six summary snapshots plus sync overview; plan detail/groups, workout detail, route/heart resources, sync state, and commands are absent. Phone-side push code exists, but remote D1 contained only pc-sync rows during this audit. PC-independent operation is not deployed or verified.
- **SuixinYiTing**：No persistent MCP data plane. Playback cache, session, and application state are device-local.
- **Daylight Journal**：Local and cloud tools use different identifiers and operations; append/status are local-only and delete is cloud-only. ChatGPT can create cloud entries while Windows is off, but cloud-created rows are not pulled into the local journal. Local mobile changes require the local sync API or wait for Windows.
- **PersonalMcpGateway**：Five diagnostics tools are reachable through a Windows-dependent secure tunnel only. Gateway diagnostics and fleet state disappear when Windows is off.
- **EchoDiary**：FastAPI AI/weather BFF exists, but no MCP server exists. Room/browser data has no verified cloud replication path.
- **VideoFlow**：Local HTTP job API exists, but no MCP adapter exists. Jobs and outputs remain local. Large videos should not be copied into D1 or MCP JSON.
- **Math Mistake Manager**：No local checkout is available for runtime verification and the public repository contains no MCP implementation. No cloud runtime sync implementation was found.

## 策略例外

- **不做手机控** (`local_only`)：The product promise explicitly forbids INTERNET permission, accounts, telemetry, and cloud sync. Only user-initiated ADB export is acceptable.
- **ChatGPT Branch Map** (`local_only`)：Source code explicitly states that imported ChatGPT exports remain in browser IndexedDB and are never uploaded.

## 云端支撑服务

| 服务 | 已部署 | 有远端备份 | 健康 |
| --- | --- | --- | --- |
| `foxlink-cloud-mcp` | True | False | HTTP 200 |
| `journal-cloud-mcp` | True | False | HTTP 200 |
| `watch-cloud-mcp` | True | False | HTTP 200 |
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
- echodiary: missing .poyi/project-platform.json
- echodiary: MCP is missing
- echodiary: sync is missing
- videoflow: missing .poyi/project-platform.json
- videoflow: MCP is missing
- videoflow: sync is missing
