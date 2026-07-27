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
- [x] BUG-004（2026-07-26，来源：舰队部署实测）用 `uv pip install --reinstall` 更新私有
  Python 里的网关 wheel 后，新文件从 %TEMP% 搬入时**丢失服务账户的继承读 ACE**
  （只剩 SYSTEM/Administrators/当前用户），服务一启动就 1067 秒崩——与 BUG-002 同类。
  —— 状态:已修（fleet/Update-PoyiFleet.ps1 装完 wheel 后强制 `icacls /T` 重刷安装树；
  同脚本还修复了"停网关前未等隧道停完导致停止被拒但脚本继续"的静默失败）。
- [x] BUG-005（2026-07-26，来源:单测遗漏）admin/fleet.py 的 ctypes SCM 查询未声明
  OpenSCManagerW/OpenServiceW 的 restype，64 位句柄被截断，所有服务状态误报 unknown
  —— 状态:已修（显式 argtypes/restype = c_void_p）。
- [x] BUG-006（2026-07-27，来源:桌面启动实测）桌面快捷方式指向 uv 虚拟环境的
  `pythonw.exe` 跳板，跳板会再交给控制台解释器，导致 PowerShell/控制台窗口观感和错误的
  生命周期依赖 —— 状态:已修（安装器复制真实 GUI-subsystem `pythonw.exe` 为
  `PoyiControlCenter.exe`，桌面/开始菜单/开机启动快捷方式全部覆盖到该启动器）。
- [x] BUG-007（2026-07-27，来源:桌面看板实测）瞬时探测失败会把整页替换成断连画面，窗口截图
  在高 DPI 下还会被裁切 —— 状态:已修（stale-while-revalidate 保留最近有效数据；应用内
  `PrintWindow` 截图启用 DPI 感知且不激活窗口）。
- [x] BUG-008（2026-07-27，来源:磁贴编辑实测）项目磁贴只能按 12 列粗粒度改宽、按三档改高，
  且唯一缩放命中区藏在右下角，拖放没有对齐反馈或收尾动画 —— 状态:已修（自由坐标与连续宽高、
  四边四角大命中区、同级/画布参考线吸附、60fps Pointer Events 更新和弹性过渡）。
- [x] BUG-009（2026-07-27，来源:桌面窗口实测）无边框看板移除了 Windows 原生缩放框，用户无法
  从四边或四角调整整个窗口 —— 状态:已修（保留全客户区外观的 `WS_THICKFRAME`、原生八方向
  `WM_NCHITTEST` 命中区、WebView 顶层八方向触发区，以及缩放期间直接跟随、松手后收尾的
  内容过渡）。
