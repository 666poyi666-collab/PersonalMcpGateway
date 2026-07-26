# 云端长在线层路线图（Cloudflare 混合架构）

> 目标（REQ-001）：**电脑关机后，ChatGPT / Claude 仍能连上各项目的 MCP**，读到已同步
> 的数据、给在线设备（手机/手表）下发命令；电脑只是设备之一，不再是唯一入口。
> 结论先行：可行。做法不是"用隧道把本机转发出去"（关机即死），而是把 **MCP Server
> 本体部署到 Cloudflare Workers**，数据存云端，设备主动向云端同步。

## 分层

```text
云端长在线层（Cloudflare Workers 远程 MCP，每项目一个）
  ├── watch-mcp.<域名>/mcp     训练/睡眠/计划 —— D1 + R2 + Durable Objects
  ├── journal-mcp.<域名>/mcp   日记 CRUD/搜索 —— D1（正文可 R2）
  ├── foxlink-mcp.<域名>/mcp   专注记录/命令队列 —— D1 + Queues
  └── (music-mcp 随心一听，后期)
设备执行层（真实数据与真实执行）
  ├── 手机 App / 手表 App —— 出站 WebSocket/HTTP 同步到云端
  └── Windows 各应用 —— 本机产生的数据上行同步
本机辅助层（现已完成，保持不动）
  └── 8 个 Windows 服务 + PoyiFleetWatchdog + 看板（开发/诊断/本机桥接/云同步代理）
```

关键约束（诚实语义）：云端 MCP 永远在线 ≠ 设备永远在线。设备离线时工具必须明确返回
`device_offline` + `lastSyncAt`，绝不假装操作成功；命令要有过期时间与幂等键。

## 分期计划

| 期 | 内容 | 前置 |
|---|---|---|
| P0 | Cloudflare 账号 + wrangler 登录 + 选定域名/子域 | **需要用户操作一次**（见下） |
| P1 | **Journal 试点**（最简单：纯 CRUD、无设备控制）：Workers MCP + D1 表（entries、revisions、idempotency）+ 鉴权（每客户端 token）；本机 JournalMcp 加"上行同步"小任务（先由本机辅助层代理推送） | P0 |
| P2 | ChatGPT / Claude 接入 Journal 云端 `/mcp` 验收：关机状态下读写日记 | P1 |
| P3 | Watch 云端化：D1（计划/摘要/幂等）+ R2（轨迹/心率大文件）+ Durable Objects（设备在线状态、WebSocket Hibernation）+ Queues（待执行命令）；手机/手表 App 增加云同步协议 | P1 经验 |
| P4 | Foxlink：云端命令队列 + 已同步历史查询；"专注会话权威状态"迁云（手机可续接会话） | P3 |
| P5 | 统一：设备身份/令牌管理、命令过期、revision 冲突策略、看板加"云端层"卡片（本机看板同时监控云端 MCP 健康） | P1-P4 |

## P0 用户需要做的事（一次性，约 10 分钟）

1. 注册/登录 Cloudflare 账号（免费版即可起步：Workers 免费额度 + D1/R2 免费层够试点）。
2. 在本机终端登录 wrangler（交互式，需要你自己完成）：
   在 Claude Code 里输入 `! npx wrangler login`（`!` 前缀直接在会话里跑命令），
   浏览器弹授权页点允许即可。
3. 告诉 Claude 用哪个子域（Workers 自带 `*.workers.dev` 也可以先用，无需买域名）。

完成 P0 后，P1（Journal 试点）可以在一个工作会话内做完并端到端验收。

## 为什么不删本机层

本机层负责：开发调试、本机数据桥接（把各 Windows 应用的数据推送云端）、局域网低延迟
调用、以及云端故障时的本地兜底。watchdog 保证"电脑开着时这一层永远活着"，这正是
云同步代理可靠性的地基。
