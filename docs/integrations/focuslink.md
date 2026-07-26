# FocusLink（专注数据）接入

FocusLink 是桌面上的专注计时器（`Desktop\time1`，Electron + SQLite）。它的三时间模型
把一次专注拆成 `activeElapsedMs`（有效专注）、`pauseElapsedMs`（暂停）、`wallElapsedMs`
（自然跨度）；数据落在 `%APPDATA%\FocusLink\focuslink.db`。

## 数据面

看板不碰 SQLite。FocusLink 自带独立的 **Foxlink MCP**（`PoyiFoxlinkMcp` Windows 服务，
`127.0.0.1:8770/mcp`），MCP 再经 Electron 回环业务 API（`127.0.0.1:18770/v1`）读账本，
所以计时事实始终只有一个持有者。链路：

```text
看板 widget -> 127.0.0.1:8770/mcp (Foxlink MCP) -> 18770/v1 业务 API -> focuslink.db
```

注意：业务 API 随 FocusLink 桌面应用启动。桌面应用没开时，widget 显示错误卡，
这是准确的状态而不是看板故障。

## 上板配置

```yaml
  - id: focus_today
    type: mcp
    title: 今日专注
    group: FocusLink
    flavor: instrument
    accent: "#007A55"
    options: {url: "http://127.0.0.1:8770/mcp", tool: foxlink_get_today_summary}
```

可用的只读工具：

| 工具 | 卡片 | 内容 |
| --- | --- | --- |
| `foxlink_get_today_summary` | stat | 有效专注时长 + 日期，注脚为会话数与暂停 |
| `foxlink_get_status` | keyvalue | 产品、版本、当前计时状态 |
| `foxlink_get_current_session` | keyvalue | 进行中的会话（无会话时为空态） |
| `foxlink_list_sessions` | keyvalue | 通用降级呈现；专用列表呈现在路线图上 |

控制类工具（`foxlink_start_focus` 等）会被看板的只读闸门直接拒绝。

## 呈现风格：时间仪器（`instrument`）

取自 FocusLink 前端规范（`FocusLink/frontend-design/FRONTEND_SPEC.md`）的真实语言：

- 连续浅色画布 + 1px 发丝线分区，**无圆角卡片墙**——卡片直角、无阴影；
- 标题下 20px × 2px 强调色短刻度，对应其导航选中态的底部刻度；
- 读数用等宽数字槽（`tabular-nums`），对应其「工业读数」仪表；
- 强调色默认取其五色预设中的翡翠 `#007A55`（深色板面自动换亮阶 `#35C493`），
  暂停/损耗语义在 FocusLink 里固定为红，看板沿用其停用色的克制原则。

## 更深入的路线

- `foxlink://analytics/today` Resource 提供比工具更细的当日分析，可做成
  专注/暂停双读数卡；
- 多日趋势（FocusLink 统计页的堆叠日柱）需要新增 list/chart 呈现器，等看板
  引入迷你柱状 kind 后接入；
- FocusLink 云测试后端（`127.0.0.1:18787`）是实验件，不作为看板数据源。
