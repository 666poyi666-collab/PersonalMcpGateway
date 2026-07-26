# 项目接入总览（Integrations）

看板的扩展面板不是一块统一模板：**每个来源项目在板上保留自己的美术语言**。
FocusLink 是发丝线与直角的时间仪器，拾光是暖纸上的衬线墨字，间歇跑是黑底荧光绿的
运动表盘。接入一个项目 = 选好它的数据面 + 用它自己的风格呈现。

| 项目 | 数据面 | 分组建议 | flavor | accent | 接入文档 |
| --- | --- | --- | --- | --- | --- |
| FocusLink（专注） | Foxlink MCP `127.0.0.1:8770/mcp` | `FocusLink` | `instrument` | `#007A55` | [focuslink.md](focuslink.md) |
| 拾光 · 日记复盘 | Journal MCP `127.0.0.1:8780/mcp` | `拾光日记` | `paper` | `#A85F27` | [journal.md](journal.md) |
| 步序 · 间歇跑 | Watch MCP `127.0.0.1:8768/mcp` | `步序 · 间歇跑` | `sport` | `#B6FF39` | [watch.md](watch.md) |
| 随心一听（手表音乐） | 暂无常驻数据面 | `开发` | `neutral` | — | [watch-music.md](watch-music.md) |
| 不做手机控 | 设计上离线，不接入 | — | — | — | [bzsjk.md](bzsjk.md) |

## 通用规则

- **只读**：`mcp` 模块只允许工具名带 get / list / summarize / search / status /
  health / capabilities 的调用；start、stop、set、append 一类在建立连接前就被拒绝。
  看板是观察面，控制永远留在各项目自己的界面和 ChatGPT 工具里。
- **loopback**：`mcp` 与 `remote` 都只接受 `127.0.0.1` / `localhost`。看板从不出网；
  需要外网数据（新闻、股票）时由你自己的本机服务去取，再喂给看板。
- **隔离**：单个模块失败只显示一张错误卡，其余照常；`mcp` 结果缓存 30 秒。
- **同一条数据路径**：`mcp` 模块调用的就是 ChatGPT 用的那些工具，不为看板另开
  第二套接口，项目方无需为看板写任何代码。

## 差异化是怎么实现的

widget 配置里的三个字段：

```yaml
group: FocusLink        # 同名 widget 聚成一节，节标题带 accent 色块
flavor: instrument      # instrument / paper / sport / neutral
accent: "#007A55"       # 该项目的识别色（也是分组标题色块）
```

每种 flavor 的视觉规范都取自来源项目的真实 token（详见各接入文档），由两个看板的
CSS 各自实现；数据层完全不感知风格。新项目想要自己的 flavor 时，在两份看板 CSS 里
加一组 `flavor-<name>` 规则即可。
