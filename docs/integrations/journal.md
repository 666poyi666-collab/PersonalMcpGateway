# 拾光 · 日记复盘接入

拾光（`C:\开发\日记复盘`）是跨端日记工作台：网页 / Android 共用一套数据，
服务端数据在项目 `data/journals.json`，并由独立 **Journal MCP**（`127.0.0.1:8780/mcp`，
`PoyiJournalTunnel` 专属隧道）向 ChatGPT 提供 7 个 `journal_*` 工具。

## 数据面

```text
看板 widget -> 127.0.0.1:8780/mcp (Journal MCP) -> journals.json
```

## 隐私红线（最重要的一条）

`journal_list_recent` 的返回里含有 `summary` —— 真实日记摘要正文。
**看板的呈现器只取 date / title / mood / tags，正文与摘要永远不上墙**；
这条写死在网关代码里（`_present_mcp_payload` 的 journal 分支）并有回归测试
（`test_journal_presenter_never_leaks_diary_body_text`）盯着。挂在墙上的看板
可以告诉你「昨天写了复盘、心情平稳」，但读日记本身请回拾光或 ChatGPT。

## 上板配置

```yaml
  - id: journal_recent
    type: mcp
    title: 最近日记
    group: 拾光日记
    flavor: paper
    accent: "#A85F27"
    options: {url: "http://127.0.0.1:8780/mcp", tool: journal_list_recent}
  - id: journal_count
    type: mcp
    title: 记录
    group: 拾光日记
    flavor: paper
    accent: "#A85F27"
    options: {url: "http://127.0.0.1:8780/mcp", tool: journal_get_status}
```

| 工具 | 卡片 | 内容 |
| --- | --- | --- |
| `journal_list_recent` | list | 标题 + 心情/标签 + 日期（日期用陶土色作时间锚点） |
| `journal_get_status` | stat | 日记总数，注脚为最近更新时间 |
| `journal_search` / `journal_get_entry` | — | 面向 ChatGPT 的读取；不建议上墙（正文风险） |

写入类（create / append / update）被只读闸门拒绝。

## 呈现风格：纸与墨（`paper`）

取自拾光的 Ink & Daylight 规范（`docs/UX-VISUAL-DIRECTION.md`）：

- 暖纸面 `#f7f4ed`、发丝线 `#e3ddce`、墨色 `#191713`；看板深色模式下换其
  暖炭纸 `#201d18` 与亮墨 `#f2ede2`，不是蓝黑反转；
- 唯一强调色赭金 `#A85F27`（深色 `#E0A458`），只用在日期锚点和列表左侧的
  2px 竖线上——对应其「正文聚焦竖线」与「陶土色时间锚点」；
- 标题与大数字用衬线（拾光默认衬线书写体的板上回声）。

## 更深入的路线

- 心情走势（mood 按日）适合日历热度条呈现，等看板引入 heat/timeline kind；
- `journal://entries/{date}` Resource 仅供 ChatGPT 复盘链路使用，看板不接。
