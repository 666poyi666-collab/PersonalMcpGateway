# 扩展面板（Board Widgets）

看板底部的「扩展面板」是留给你自己的区域。MCP 项目状态只是看板的第一批内容；日程、
项目进度、新闻、股票、专注统计这类东西，以后都通过这里接进来，网页版和桌面版同时生效。

不需要写代码，也不需要重启任何服务：**编辑一个 YAML 文件，保存，看板几秒内自动更新。**

## 文件在哪里

在网关数据目录新建 `board-widgets.yaml`：

| 运行方式 | 数据目录 |
| --- | --- |
| Windows 服务（默认部署） | `C:\ProgramData\Poyi\PersonalMcpGateway` |
| 开发模式 (`uv run personal-mcp-gateway serve`) | 同上，除非设置了 `PERSONAL_MCP_DATA_DIR` |

没有这个文件时，扩展面板会显示一张「如何开始」的说明卡片——这不是错误。

## 最小例子

```yaml
widgets:
  - id: hello
    type: text
    title: 便签
    options:
      body: 这是我的第一个模块。
```

保存后等一次刷新（4 秒），卡片就会出现在两个看板上。

## 内置模块类型

每个模块都有 `id`（小写字母/数字/下划线，且唯一）、`type`、`title`，可选 `subtitle`，
其余参数放在 `options` 里。

### text — 文字便签

```yaml
  - id: memo
    type: text
    title: 本周重点
    options:
      body: |
        完成 Gateway 的 72 小时稳定性测试。
        周五前给 Journal MCP 提交新的复盘模板。
```

### agenda — 日程

```yaml
  - id: agenda
    type: agenda
    title: 最近日程
    options:
      file: agenda.yaml   # 可省略；相对路径相对数据目录
      limit: 6            # 可省略；最多显示几条
```

日程写在数据目录的 `agenda.yaml` 里，一行一件事：

```yaml
- date: 2026-07-27
  time: "14:30"
  title: 和供应商开会
  note: 记得带合同
- date: 2026-08-01
  title: 交季度总结
```

已经过去的日期自动隐藏，今天显示为「今天」，明天显示为「明天」。
`time` 请加引号（`"14:30"`）——不加引号时 YAML 会把它读成一个数字，
看板虽然能纠正常见写法，但引号最稳妥。

### projects — 项目进度

列出你本地的项目仓库，看板显示每个项目的分支、最近一次提交和未提交改动数：

```yaml
  - id: projects
    type: projects
    title: 项目进度
    options:
      repos:
        - C:\dev\my-first-project
        - C:\dev\another-project
```

只做只读的 `git` 查询，不会改动任何仓库；结果缓存 60 秒。路径不存在或不是 Git
仓库时，对应条目会说明原因，其余条目不受影响。

### mcp — 直连各项目的 MCP（比状态更深的数据）

对独立 MCP 项目（FocusLink / 拾光 / 间歇跑），看板可以直接调用它们的只读工具——
和 ChatGPT 用的是同一批工具，项目方不用为看板写任何代码：

```yaml
  - id: focus_today
    type: mcp
    title: 今日专注
    group: FocusLink
    flavor: instrument
    accent: "#007A55"
    options:
      url: http://127.0.0.1:8770/mcp
      tool: foxlink_get_today_summary
      # args: {days: 7}    # 可选的工具参数
```

规则：

- 只允许工具名带 get / list / summarize / search / status / health / capabilities
  的**只读调用**；start、stop、set、append 一类在建立连接前就被拒绝。
- 仍然只接受 loopback 地址；结果缓存 30 秒；单次调用预算 9 秒（汇总类工具要
  遍历真实历史，可能较慢）。
- 已知工具有专门排版（今日专注、训练汇总、最近睡眠、最近日记等），未知的只读
  工具降级为键值对呈现。每个项目的工具清单见 `docs/integrations/`。

### remote — 自定义数据源

这是给你自己的程序留的接口：任何本机服务只要返回一段约定的 JSON，就能上看板。

```yaml
  - id: focus
    type: remote
    title: 今日专注
    options:
      url: http://127.0.0.1:8790/widget
```

服务端返回：

```json
{
  "kind": "stat",
  "subtitle": "来自专注计时器",
  "data": { "value": "3.5 小时", "label": "今日累计", "note": "比昨天多 40 分钟" }
}
```

`kind` 决定卡片长什么样，`data` 的字段随 `kind` 变化：

| kind | data 字段 | 适合 |
| --- | --- | --- |
| `stat` | `value`、`label`、`note?` | 一个大数字（专注时长、账户余额） |
| `list` | `items: [{title, subtitle?, value?, state?}]`、`empty?` | 一列条目（新闻、待办、股票列表） |
| `keyvalue` | `pairs: [{label, value}]` | 几对键值（指标汇总） |
| `text` | `body`（`\n` 分段） | 一段文字 |

`state` 只接受 `online` / `degraded` / `offline`，会渲染成和项目状态一致的
圆形/三角/菱形标记。字段有长度与条数上限，超出部分被截断——一个出问题的本机服务
不能刷爆看板。

**只允许 loopback 地址**（`127.0.0.1` / `localhost` / `::1`）。看板永远不会因为一个
widget 而访问外网；新闻、股票这类需要外网的数据，由你自己的本机服务去取，再以上面的
JSON 形式交给看板。

## 分组与风格（差异化呈现）

每个 widget 还可以带三个可选字段，让不同项目在同一块板上保留各自的美术语言：

```yaml
group: 拾光日记        # 相同 group 的卡片聚成一节，节标题带色块
flavor: paper          # neutral（默认）/ instrument / paper / sport
accent: "#A85F27"      # 该项目的识别色（十六进制）
```

| flavor | 语言 | 来源 |
| --- | --- | --- |
| `instrument` | 发丝线直角、标题短刻度、等宽工业读数 | FocusLink「时间仪器」 |
| `paper` | 暖纸衬线墨字、赭金竖线、陶土色日期锚点 | 拾光 Ink & Daylight |
| `sport` | 常暗表盘、荧光绿读数、手表屏圆角 | 间歇跑 OWW221 表盘 |
| `neutral` | 看板自己的样子 | 默认 |

各项目的完整接入方案（数据面、工具清单、风格依据）见
[docs/integrations/](integrations/README.md)。

## 规则

- 一个模块出错只影响它自己：错误以卡片形式显示原因，其余模块照常刷新。
- `board-widgets.yaml` 本身写错时，扩展面板显示一张说明卡片，其余看板功能不受影响。
- 刷新节奏：text 基本静态、agenda 15 秒、projects 60 秒、remote 跟随看板刷新；
  修改配置文件立即生效。

## 故障排查

| 卡片显示 | 原因 |
| --- | --- |
| `board-widgets.yaml 配置无效` | YAML 语法错误、`type`/`flavor` 拼错、`accent` 不是十六进制、`id` 重复或不合规 |
| `remote/mcp 模块只接受 loopback 地址` | `url` 不是 `127.0.0.1` / `localhost` |
| `数据源无法访问` | 目标服务没启动，或 2.5 秒内没有响应 |
| `数据源返回的 kind 不受支持` | JSON 里的 `kind` 不在上表中 |
| `... 不是只读工具` | `mcp` 模块只允许 get/list/summarize 类调用 |
| `MCP 服务无法访问或响应超时` | 对应 MCP 服务没启动，或 9 秒内没有完成 |
| `手机/手表当前离线`、`手机/手表响应超时` | 对应设备或后台业务服务暂不可达；卡片保持中性状态并在 30 秒后自动重试 |
| `工具执行返回错误` | MCP 返回了离线/超时以外的业务错误，需要查看对应项目的服务状态和日志 |
| `未找到 git` | `projects` 模块需要 PATH 里有 `git` |
