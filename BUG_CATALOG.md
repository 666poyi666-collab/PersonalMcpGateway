# BUG_CATALOG

> 登记规则见 `C:\开发\MAINTENANCE.md`。跨项目问题也先记在这里（网关是全舰队运维归属方）。

- [x] BUG-001（2026-07-26，来源：舰队诊断）看板 `DEFAULT_TARGETS` 把 watch/foxlink/journal
  三家的隧道就绪端口互相标错（watch→8878、foxlink→8887、journal→8880；实测应为
  8880/8878/8887），导致"Watch 降级、Journal 正常"等状态张冠李戴 —— 状态:已修
  （src/personal_mcp_gateway/admin/dashboard.py + ProgramData 覆盖文件 dashboard-targets.yaml）
- [x] BUG-002（2026-07-26，来源：事件日志 + recent-errors）`C:\ProgramData\Poyi\*` ACL 漂移后，
  开机时 Gateway 因 `PermissionError: gateway.jsonl` 秒崩（SCM 记录 1070 次），Watch MCP 同类
  崩 1123 次，Foxlink 报 0x20000001 后因**未配置任何失败重启动作**躺平整晚 —— 状态:已缓解
  （fleet 层：watchdog 开机重刷 ACL 基线 + 全部 8 服务统一 sc failure 无限重启；代码层
  logging 降级早前已修）。根因遗留问题：各项目安装器各自 `icacls /inheritance:r` 会互相
  破坏，后续应统一到一个安装约定。
- [x] BUG-003（2026-07-26，来源：舰队演习）隧道 tunnel-client 在其 MCP 宕机期间完成 OAuth
  发现失败后**不再重试**，MCP 恢复后隧道仍长期 503（Foxlink 隧道自开机 19:28 滞留到 21:28
  被 watchdog 强制重启才恢复）—— 状态:已缓解（watchdog 连续 10 次就绪失败即重启隧道服务；
  上游 tunnel-client 行为无法修改）。
