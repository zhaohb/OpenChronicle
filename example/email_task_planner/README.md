# 邮件待办与日历提醒（email_task_planner）

从 OpenChronicle 已捕获的邮件窗口、邮件网页、回复草稿中抽取待办事项与时间节点，并生成：

- Markdown 报告：方便人工核对
- `.ics` 日历文件：导入 Outlook / Windows Calendar / Apple Calendar 后触发提醒

## 解决什么问题

邮件里的待办经常不是标准任务格式，而是散落在正文、回复草稿、转发说明里：

- “请明天下午三点前确认报价”
- “麻烦周五前补一版风险评估”
- “我会在 5/12 之前把材料发你”

OpenChronicle 已经能捕获用户桌面上出现过的邮件窗口和用户 typed 的回复草稿。本 example 在不接入邮箱账号的前提下，把这些内容加工成待办清单和可导入日历的提醒。

## 能力边界

- **不接 IMAP / Microsoft Graph / Gmail API**：不读取完整邮箱，不需要邮箱授权。
- **只读 OpenChronicle memory**：只分析已经出现在桌面并被写入 `event-*.md` 的邮件内容。
- **不常驻后台**：本应用只生成 `.ics`，提醒由用户导入后的日历客户端负责。
- **不强行造截止时间**：没有明确时间节点的事项只进入 Markdown，不写入日历。

## 用法

```bash
# 今天邮件待办
python -m email_task_planner.planner
# 等价于
oc-mailtasks

# 昨天
oc-mailtasks --yesterday

# 指定单日
oc-mailtasks --date 2026-05-09

# 指定区间
oc-mailtasks --since 2026-05-01 --until 2026-05-09

# 生成 Markdown + ICS
oc-mailtasks --since 2026-05-01 --until 2026-05-09 \
    --output ./mailtasks/tasks.md \
    --calendar-out ./mailtasks/tasks.ics

# 提前 2 小时提醒
oc-mailtasks --remind-before 120

# 只有日期没有具体时间时，默认设置为当天 09:00
oc-mailtasks --default-due-time 09:00

# 切换本地 Ollama OpenVINO 上的另一个模型
oc-mailtasks --model qwen2.5:14b
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date YYYY-MM-DD` | — | 单日 |
| `--since` / `--until` | 今天/今天 | 区间，含两端 |
| `--yesterday` | off | `--date <昨天>` 的快捷方式 |
| `-o` / `--output` | `./mailtasks/email-tasks-<日期>.md` | Markdown 输出 |
| `--calendar-out` | `./mailtasks/email-tasks-<日期>.ics` | 日历文件输出 |
| `--remind-before` | `30` | 日历提醒提前分钟数 |
| `--default-due-time` | `09:00` | 邮件只写日期时补齐的默认时间 |
| `--model` | `qwen2.5:7b` | 任何 Ollama 已 pull 的模型 |
| `--mcp-url` | `http://127.0.0.1:8742/mcp` | 覆盖 OpenChronicle MCP |
| `--verbose` | off | DEBUG 日志 |

## 输出示例

```markdown
# 邮件待办与时间节点 · 2026-05-09

_来源：OpenChronicle event-daily entries（共 28 条；邮件候选 3 条），生成时间 2026-05-09 11:30_
_日历文件：`mailtasks/email-tasks-2026-05-09.ics`_

## 一、有时间节点的邮件待办

| 时间节点 | 负责人 | 待办 | 来源 | 置信度 |
|---|---|---|---|---|
| 2026-05-10 09:00 | me | 回复客户确认 5/12 14:00 上门时间。 | 客户现场支持时间确认 | high |

### 1. 回复客户确认 5/12 14:00 上门时间。

- **负责人**：me
- **内容**：回复客户确认 5/12 14:00 上门时间。
- **来源应用**：Outlook
- **邮件/窗口标题**：客户现场支持时间确认
- **时间节点**：2026-05-10 09:00（时间由默认值补齐）
- **置信度**：high
- **来源 session**：`sess_4a2f1c`
- **证据**：
  - Outlook reply typed "我们 5/12 14:00 在客户现场见"
  - email body includes "请明天前确认"
```

对应 `.ics` 会包含：

```text
BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:邮件待办：回复客户确认 5/12 14:00 上门时间。
DTSTART:20260510T090000
BEGIN:VALARM
TRIGGER:-PT30M
ACTION:DISPLAY
END:VALARM
END:VEVENT
END:VCALENDAR
```

## 工作原理

```
event-YYYY-MM-DD.md
        │
        ▼
  邮件相关 entry 过滤
        │
        ▼
  LLM Pass (email_task_extract.md)
        │
        ▼
  EmailTask objects
        ├── render_markdown → .md
        └── export_ics      → .ics
```

过滤策略先缩小上下文，再交给 LLM：

- app 命中：`Outlook`、`Mail`、`Thunderbird`、`Foxmail`、`Gmail` 等
- 文本命中：`邮件`、`收件箱`、`回复`、`转发`、`subject`、`deadline`、`due`、`请在`、`截止` 等
- 浏览器中的 Gmail / 企业邮箱依赖窗口标题、URL 或页面文本中出现 mail 相关证据

## 关键文件

| 文件 | 作用 |
|---|---|
| `planner.py` | CLI 入口，解析参数，写 Markdown 和 `.ics` |
| `extractor.py` | 拉取 event-daily、过滤邮件上下文、调用 LLM、渲染 Markdown |
| `calendar_export.py` | 生成 RFC5545 `.ics` 日历文件 |
| `prompts/email_task_extract.md` | 邮件待办抽取 prompt |

## 和 meeting_task_digest 的区别

`meeting_task_digest` 是“从桌面活动中推断会议/待办”，邮件只是它可能看到的一种上下文。

`email_task_planner` 则专注邮件：只从邮件窗口、邮件网页和回复草稿中抽取明确待办与时间节点，并生成可导入日历的提醒文件。
