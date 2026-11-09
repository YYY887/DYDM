# static —— 截图放置目录

把运行截图按下面的文件名丢进来，README 里的引用就会自动显示：

| 文件名 | 对应内容 | 建议截什么 |
| --- | --- | --- |
| `terminal.png` | 终端实时输出 | `uv run main.py <房间号>` 跑起来的样子，能同时看到弹幕（青）、礼物（金）、进场（灰）、点赞（紫） |
| `gift.png` | 礼物消息 | `uv run main.py <房间号> --gift-only`，或 `uv run gift_probe.py` 抓到礼物时的字段对照 |
| `protocol.png` | 协议/字段 | `uv run selfcheck.py <房间号>` 的分层输出，或抓包里的 `im/fetch` 请求 |
| `csv.png` | 落盘结果 | `--jsonl/--csv` 之后 Excel 打开 CSV 的效果 |

截图建议：

- Windows 终端开启 UTF-8（`chcp 65001`）再截，中文和符号不会乱
- 截图宽度别太窄，消息行（时间 + 标签 + 昵称 + 内容）尽量完整
- 想固定颜色输出，别把 stdout 重定向到文件（重定向会自动关闭颜色）
