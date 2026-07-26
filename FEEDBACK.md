# FEEDBACK

## 需求池

- [ ] REQ-001（2026-07-26，来源：自己使用 + ChatGPT 方案讨论）电脑关机后 MCP 仍要在线：
  把各项目 MCP Server 部署到 Cloudflare Workers（D1/R2/Durable Objects/Queues），设备主动
  向云端同步；本机层降级为开发/诊断/桥接。方案见 docs/cloud-architecture.md
  —— 热度:2 状态:进行中（P1 journal 云端 CRUD 已上线实测；P3/P4 watch+foxlink 快照镜像已上线，watchdog 每 5 分钟自动同步；剩余：手机/手表 App 直连云端、命令队列、正式 OAuth）
- [x] REQ-002（2026-07-26，来源：自己使用）Journal 同步端口 8781 的暴露面复核完成：
  该端口是手机/平板所需的 LAN 业务 API，不提供 MCP；Windows 防火墙仅允许 Private
  配置文件的 LocalSubnet，业务接口强制 Bearer。现场验证 `/healthz` 为 200、匿名
  `/v1/status` 为 401、`/mcp` 为 404，因此保留 `0.0.0.0` 绑定，不破坏移动端同步
  —— 热度:1 状态:已完成

## 已完成

- [x] REQ-000（2026-07-26，来源：自己使用）看板即唯一运维入口：舰队状态（服务级）与
  一键修复叠加进 Poyi Control Center，修复走触发文件由看护服务执行，全程无 PowerShell
  窗口、无 UAC → fleet 层 + 看板集成实现
