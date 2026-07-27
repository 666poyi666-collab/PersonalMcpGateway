# 步序 · 间歇跑（手表训练与睡眠）接入

间歇跑（`C:\开发\手表开发`）是 OPPO Watch 4 Pro（OWW221，378×496）上的独立训练应用，
带手机伴侣。训练历史（GPS 轨迹、心率样本、步数、配速、阶段计划）与系统睡眠数据都
经手机汇聚，由独立 **Watch MCP**（`PoyiWatchMcp`，`127.0.0.1:8768/mcp`）向外提供。

## 数据面

```text
看板 widget -> 127.0.0.1:8768/mcp (Watch MCP) -> 手机 8766 API -> BLE/LAN -> 手表
```

手机是唯一业务门面；手表离线或手机不在局域网时，工具会失败或返回连接状态，
看板如实显示错误卡或降级数据，30 秒后自动重试。汇总类工具要遍历手机上的历史，
偶尔较慢——`mcp` 模块为此有 9 秒预算。

## 上板配置

```yaml
  - id: watch_workouts
    type: mcp
    title: 训练汇总
    group: 步序 · 间歇跑
    flavor: sport
    accent: "#B6FF39"
    options: {url: "http://127.0.0.1:8768/mcp", tool: watch_summarize_workouts}
  - id: watch_sleep
    type: mcp
    title: 最近睡眠
    group: 步序 · 间歇跑
    flavor: sport
    accent: "#B6FF39"
    options: {url: "http://127.0.0.1:8768/mcp", tool: watch_get_latest_sleep}
```

| 工具 | 卡片 | 内容 |
| --- | --- | --- |
| `watch_summarize_workouts` | keyvalue | 训练次数 / 总距离 / 累计活动 / 平均心率 / 最近计划（组·名） |
| `watch_get_latest_sleep` | keyvalue | 睡眠时长 / 评分 / 心率区间 |
| `watch_get_status` | keyvalue | 通用降级呈现：手机与手表连接状态 |
| `watch_summarize_sleep` | keyvalue | 多日睡眠汇总（通用呈现） |

控制类（start / pause / resume / stop / set / select / delete / sync）全部被只读闸门拒绝；
远程开跑属于 ChatGPT 工具链，不属于挂在墙上的看板。

## 呈现风格：运动表盘（`sport`）

手表应用的系统强调色是荧光绿 `#B6FF39`（`app/src/main/res/values/styles.xml`），
运行在黑色 AMOLED 表盘上。板上的对应物：

- 始终深色的 AMOLED 卡面——亮色看板里嵌一块「表盘」，就像手表躺在桌上；
- 标题使用 `INTERVAL ENGINE / OWW221` 身份带，荧光绿只标识步序和关键运动读数；
- 训练舱以总距离为主读数，同时展示训练次数、累计活动、平均心率和最近计划；
- 恢复舱只使用真实睡眠数据，以 0–100 圆环展示系统睡眠评分，并列睡眠时长和心率区间；
- Watch MCP 超时或手机/手表离线时，仪表结构保持稳定，仅把数据锁定状态切为
  `PARTIAL SYNC` / `SYNC WAIT`，不伪造读数；
- 磁贴放大、缩小或拉成横条时按宽高密度切换完整仪表、紧凑仪表和只读数模式，
  主读数与操作手柄始终不重叠。

## 更深入的路线

- 轨迹与逐秒心率在 `watch://workouts/{id}/route|heart/{cursor}` Resources 里，
  刻意不经普通工具整批返回；将来做迷你轨迹缩略图时需要新的呈现器与游标拉取；
- `watch_list_workouts` 的专用列表呈现（每次训练一行：日期/距离/配速）是下一个
  自然增量。
