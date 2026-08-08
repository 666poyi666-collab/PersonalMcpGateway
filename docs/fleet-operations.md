# 舰队运维（Fleet Operations）

这台机器上共有 4 个 MCP 项目 × 2 个 Windows 服务（MCP 本体 + Secure Tunnel），共 8 个服务。
`fleet\` 目录提供统一的保活、自愈与一键修复层，让它们**开机自动全部拉起、崩了自动重启、
降级自动恢复**，供 ChatGPT / Claude 随时接入。

## 端口与服务对照表（唯一权威）

| 项目 | MCP 服务 | MCP 健康 | 隧道服务 | 隧道就绪 |
|---|---|---|---|---|
| Personal Gateway | PoyiPersonalMcpGateway | `127.0.0.1:8761/healthz` | OpenAISecureMcpTunnel | `127.0.0.1:8877/readyz` |
| Watch MCP | PoyiWatchMcp | `127.0.0.1:8768/healthz` | PoyiWatchTunnel | `127.0.0.1:8880/readyz` |
| Foxlink MCP | PoyiFoxlinkMcp | `127.0.0.1:8770/healthz` | FoxlinkSecureMcpTunnel | `127.0.0.1:8878/readyz` |
| Journal MCP | PoyiJournalMcp | `127.0.0.1:8780/healthz` | PoyiJournalTunnel | `127.0.0.1:8887/readyz` |

注意：2026-07-26 之前看板默认配置把 watch/foxlink/journal 三家的隧道端口标错位了
（BUG-001）。上表按各 tunnel-client `/api/status` 实测校正，
`%ProgramData%\Poyi\PersonalMcpGateway\dashboard-targets.yaml` 覆盖文件与本表一致。

## 三层保活设计

1. **SCM 失败动作**（进程死亡）：全部 8 个服务 `sc failure` 配置为 5s/15s/60s 重启、
   60s 档永久重复、每日重置计数，且 `failureflag=1`。进程一死 SCM 就拉。
2. **PoyiFleetWatchdog 服务**（降级与滞留）：每 30 秒巡检一轮——
   - 服务处于 `Stopped` → 直接启动（SCM 对"启动即失败太多次后放弃"的服务无能为力，
     Foxlink 2026-07-26 就是这样躺了一晚）。
   - 服务在跑但健康探测连续失败（MCP 连续 4 次 / 隧道连续 10 次）→ 强制重启该服务，
     必要时清理占着端口不放的孤儿进程。隧道在其 MCP 不健康时不计失败（避免连坐）。
   - 冷却 10 分钟/次、每小时最多 5 次，超了退避 30 分钟并写事件日志，防止风暴。
   - 启动时（即每次开机）先把 4 个项目 `ProgramData` 目录的 ACL 基线重刷一遍
     （SYSTEM/Administrators 完全控制 + 各服务账户 Modify，只增不减）——
     ACL 漂移正是 2026-07-26 开机崩溃循环 1000+ 次的根因（BUG-002）。
3. **开机自启**：8 个服务 + watchdog 全部 `delayed-auto`。就算某个服务开机起不来，
   watchdog 会修 ACL 后再拉一次。

## 日常操作

**首选入口就是 Poyi Control Center 看板本身**（桌面版与 `http://127.0.0.1:8761/admin/status`）：

- 每张项目卡片会显示探测结果；探测失败时区分"服务已停止 / 服务未安装 / 不可用"。
- 桌面管理页平时只显示一句总状态，详细调用与异常放在「运行记录」，避免把巡检指标
  长期堆在首屏。
- 只有连接出问题时，提示条才显示 **尝试自动修复**。它会把请求写入
  `C:\ProgramData\Poyi\FleetWatchdog\triggers\`，由 SYSTEM 权限的看护服务执行强制
  修复；不弹 UAC。每分钟最多执行一次，维护模式（maintenance.flag）下忽略。

命令行备用入口：

| 想做什么 | 怎么做 |
|---|---|
| 看全家状态 | 双击 `fleet\Status-PoyiFleet.cmd`（无需管理员），或打开 Poyi Control Center 看板 |
| 一键修复全部 | 连接异常时点桌面管理页的「尝试自动修复」；或双击 `fleet\Repair-PoyiFleet.cmd`（弹 UAC）——幂等，随时可跑 |
| 改完代码重新部署 | 管理员运行 `fleet\Update-PoyiFleet.ps1`（构建 wheel → 装入私有 Python → 重启网关与看护；会自动补 ACL、更新期间挂 maintenance.flag） |
| 暂停 watchdog（维护/装新版本时） | 建一个空文件 `C:\ProgramData\Poyi\FleetWatchdog\maintenance.flag`，删掉即恢复 |
| 查 watchdog 干了什么 | `C:\ProgramData\Poyi\FleetWatchdog\watchdog.log`；重启动作也写入 Windows 事件日志（来源 `PoyiFleetWatchdog`，事件 9001–9005） |
| 调巡检节奏/阈值 | 改 `C:\Program Files\Poyi\FleetWatchdog\fleet-config.json` 后重启 PoyiFleetWatchdog 服务（仓库 `fleet\fleet-config.json` 是源头，改完用 Repair 重新部署） |

## 新增一个 MCP 项目时

1. 在 `fleet\fleet-config.json` 的 `projects` 里加一条（服务名、健康 URL、数据目录、
   需要 Modify 权限的服务账户）。
2. 在看板 `dashboard-targets.yaml`（或 `admin/dashboard.py` 默认表）加对应卡片，
   端口以新项目 tunnel-client `/api/status` 实测为准。
3. 双击 `Repair-PoyiFleet.cmd` 重新部署。

## 已知边界

- 隧道是**出站**连接（连 `api.openai.com`），机器断网或代理没起来时隧道就绪会失败；
  watchdog 会按退避节奏持续重试，网络恢复后自愈，无需人工。
- 关机状态下本机服务无法被远端调用（物理规律）；需要 24×7 可用请保持这台机器
  常开或后续迁移到常开设备/云端。
- `verify-windows-interactive.ps1`（重启/睡眠唤醒完整验收）仍值得在下次重启后跑一次。
