# FEEDBACK

## 需求池

- [ ] REQ-001（2026-07-26，来源：自己使用 + ChatGPT 方案讨论）电脑关机后 MCP 仍要在线：
  把各项目 MCP Server 部署到 Cloudflare Workers（D1/R2/Durable Objects/Queues），设备主动
  向云端同步；本机层降级为开发/诊断/桥接。方案见 docs/cloud-architecture.md
  —— 热度:2 状态:进行中（P1 journal 云端 CRUD 已上线实测；P3/P4 watch+foxlink 快照镜像已上线，watchdog 每 5 分钟自动同步；剩余：手机/手表 App 直连云端、命令队列、正式 OAuth）
- [ ] REQ-002（2026-07-26，来源：自己使用）Journal 同步端口 8781 目前绑定 0.0.0.0 暴露
  局域网，应收敛到 127.0.0.1 或加鉴权（属 JournalMcp 项目，登记在此防遗忘）
  —— 热度:1 状态:待评估

## 已完成

- [x] REQ-000（2026-07-26，来源：自己使用）看板即唯一运维入口：舰队状态（服务级）与
  一键修复叠加进 Poyi Control Center，修复走触发文件由看护服务执行，全程无 PowerShell
  窗口、无 UAC → fleet 层 + 看板集成实现
