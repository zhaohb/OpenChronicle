# OpenChronicle Examples · 四个应用的使用手册

> 四个**只读消费 OpenChronicle 本地记忆**的上层应用，把已经沉淀好的桌面工作记忆（`event-*.md`、`project-*.md`、`tool-*.md`、`Observed regularity:` …）加工成更具体的业务产物：会议/任务报告、邮件待办提醒、员工交接初稿、桌面活动长期记忆。
>
> 全部支持 Windows 与 macOS，全部默认走**本地 Ollama OpenVINO**，无需联网，无需把屏幕内容外发。

---

## 0. 目录

- [1. 四个应用一览](#1-四个应用一览)
- [2. 一次性环境准备](#2-一次性环境准备)
- [3. 应用一：`meeting_task_digest`（会议与任务沉淀）](#3-应用一meeting_task_digest会议与任务沉淀)
- [4. 应用二：`handover_assistant`（员工交接助手）](#4-应用二handover_assistant员工交接助手)
- [5. 应用三：`activity_chronicler`（桌面活动长期记忆）](#5-应用三activity_chronicler桌面活动长期记忆)
- [6. 应用四：`email_task_planner`（邮件待办与日历提醒）](#6-应用四email_task_planner邮件待办与日历提醒)
- [7. 四个应用怎么搭配用](#7-四个应用怎么搭配用)
- [8. 公共参数速查](#8-公共参数速查)
- [9. 故障排查（FAQ）](#9-故障排查faq)
- [10. 共享模块与可定制点](#10-共享模块与可定制点)

---

## 1. 四个应用一览


| 应用                    | 用途                        | 时间窗       | 主驱动             | 默认输出路径                                           | 入口命令           |
| --------------------- | ------------------------- | --------- | --------------- | ------------------------------------------------ | -------------- |
| `meeting_task_digest` | 把每天/每周的会议与待办整理成可读报告       | 1 天 ~ 1 周 | LLM 抽取          | `./digests/meeting-YYYY-MM-DD.md`                | `oc-digest`    |
| `handover_assistant`  | 一键产出员工离职/休假/转岗交接初稿        | 1 ~ 3 个月  | LLM 综合          | `./handover/handover-YYYY-MM-DD.md`              | `oc-handover`  |
| `activity_chronicler` | 桌面活动的长期记忆：周报/月报/长期模式      | 1 周 ~ N 周 | 统计 + LLM        | `./recaps/activity-week-YYYY-WNN.md`             | `oc-recap`     |
| `email_task_planner`  | 从邮件窗口/草稿中抽取待办和时间节点，导出日历提醒 | 1 天 ~ 1 周 | LLM 抽取 + ICS 导出 | `./mailtasks/email-tasks-YYYY-MM-DD.md` + `.ics` | `oc-mailtasks` |


四个应用共享 `shared/`（MCP 客户端 + LLM 客户端 + 数据加载器），共用一份 `pyproject.toml` 和 `.env`。

---

## 2. 一次性环境准备

### 2.1 启动 OpenChronicle daemon

四个应用都通过 OpenChronicle 的只读 MCP 服务取数据，**先确认 daemon 已经在跑**：

```bash
cd C:\hongbo\UX\OpenChronicle

# 前台运行（推荐第一次跑，能看到日志）
openchronicle start --foreground

# 另开一个终端确认状态
openchronicle status
```

确认下面三件事：

1. `~/.openchronicle/memory/` 下已经出现若干 `event-YYYY-MM-DD.md` 文件
2. MCP 服务监听 `http://127.0.0.1:8742/mcp`
3. 至少跑了几个小时，timeline / reducer / classifier 阶段已经产出过

### 2.2 启动本地 Ollama OpenVINO

四个应用默认连 `http://127.0.0.1:11434/v1/chat/completions`。启动前自检一下：

```bash
curl http://127.0.0.1:11434/api/version
curl http://127.0.0.1:11434/api/show -d '{"model":"qwen2.5:7b"}'
```

`api/show` 返回的 `capabilities` 中要包含 `tools`，才能保证 JSON 模式稳定。

### 2.3 安装 example 包

```bash
cd C:\hongbo\UX\OpenChronicle\example

# 方式一：uv（推荐）
uv sync

# 方式二：pip
python -m pip install -e .

# 复制并按需修改环境变量（默认值通常不需要改）
copy .env.example .env
```

安装成功后会得到四个命令行入口：`oc-digest`、`oc-handover`、`oc-recap`、`oc-mailtasks`。

### 2.4 `.env` 关键变量

```ini
# OpenChronicle MCP 端点
OC_MCP_URL=http://127.0.0.1:8742/mcp

# 本地 LLM（OpenAI 兼容）
OC_LLM_BASE_URL=http://127.0.0.1:11434/v1
OC_LLM_MODEL=qwen2.5:7b
OC_LLM_API_KEY=ollama
```

httpx 客户端强制 `trust_env=False`，会自动忽略 `HTTP_PROXY` / `HTTPS_PROXY`，企业代理拦截 localhost 导致的 `UNEXPECTED_EOF` 不会出现，**不需要手动清代理变量**。

---

## 3. 应用一：`meeting_task_digest`（会议与任务沉淀）

把 `event-YYYY-MM-DD.md` 二次加工成"今日 / 本周会议与待办报告"。

**典型场景：**

- 每天早上一份昨日待办清单
- 每周五写周报前，先生成一份会议 + 决策 + 待办的底稿
- 写完一天工作回顾时不再翻 IM、邮件、文档

### 3.1 用法

```bash
# 今天
python -m meeting_task_digest.digest
# 等价于
oc-digest

# 昨天
oc-digest --yesterday

# 指定单日
oc-digest --date 2026-05-08

# 指定区间（写周报）
oc-digest --since 2026-05-04 --until 2026-05-10

# 指定输出路径
oc-digest --since 2026-05-04 --until 2026-05-10 -o ./out/week-report.md

# 切换本地 Ollama 模型
oc-digest --yesterday --model qwen2.5:14b

# 覆盖 MCP URL（极少用到）
oc-digest --mcp-url http://127.0.0.1:8742/mcp

# 详细日志
oc-digest --yesterday --verbose
```

### 3.2 参数


| 参数                    | 默认                          | 说明                   |
| --------------------- | --------------------------- | -------------------- |
| `--date YYYY-MM-DD`   | —                           | 单日                   |
| `--since` / `--until` | 今天/今天                       | 区间，含两端               |
| `--yesterday`         | off                         | `--date <昨天>` 的快捷方式  |
| `-o` / `--output`     | `./digests/meeting-<日期>.md` | Markdown 输出          |
| `--model`             | `qwen2.5:7b`                | 任何 Ollama 已 pull 的模型 |
| `--mcp-url`           | `http://127.0.0.1:8742/mcp` | 覆盖 OpenChronicle MCP |
| `--verbose`           | off                         | DEBUG 日志             |


### 3.3 输出片段

```markdown
# 会议与任务沉淀 · 2026-05-08
_来源：OpenChronicle event-daily entries（共 12 条），生成时间 2026-05-09 09:30_

## 一、会议汇总
| 日期 | 时间 | 主题 | 应用 | 参与人 |
|---|---|---|---|---|
| 2026-05-08 | 10:00-10:45 | Q3 路线图同步 | Microsoft Teams | Alice, Bob, 我 |

### 📌 Q3 路线图同步
**关键决策：**
- 把"统一登录"功能从 Q3 推迟到 Q4
**待办事项：**
- [me] 周五前给出登录改动的影响面评估

## 二、独立待办
| 负责人 | 任务 | 截止 | 上下文 | 置信度 |
|---|---|---|---|---|
| me | 回邮件确认 5/12 上门时间 | 2026-05-10 | Outlook 邮件草稿 | high |
```

详见 `[meeting_task_digest/README.md](./meeting_task_digest/README.md)`。

---

## 4. 应用二：`handover_assistant`（员工交接助手）

把长期沉淀的 `project-*.md` / `tool-*.md` / `person-*.md` / `org-*.md` 与最近若干周的 event-daily 综合成一份"交接初稿"。

**典型场景：**

- 员工离职 / 转岗，需要一份能交给接手人的"项目状态 + 风险 + 联系人"清单
- 员工长假前留底稿，回来时也用得上
- 项目换人时，新人快速建立全局视图

### 4.1 用法

```bash
# 默认窗口：最近 30 天，所有活动项目
python -m handover_assistant.handover
# 等价于
oc-handover

# 指定时间窗
oc-handover --since 2026-04-01 --until 2026-05-09
oc-handover --days 60

# 只生成单个项目的交接
oc-handover --project openchronicle

# 指定文档署名
oc-handover --owner "张三"

# 指定输出
oc-handover --output ./out/handover.md

# 切换更大模型（综合阶段建议 14B 起步）
oc-handover --model qwen2.5:14b
```

### 4.2 参数


| 参数                    | 默认                            | 说明                       |
| --------------------- | ----------------------------- | ------------------------ |
| `--days N`            | 30                            | 最近 N 天为窗口                |
| `--since` / `--until` | —                             | 显式区间，覆盖 `--days`         |
| `--project <slug>`    | 全部                            | 只跑指定 `project-<slug>.md` |
| `--owner`             | `我`                           | 文档头部的署名                  |
| `-o` / `--output`     | `./handover/handover-<日期>.md` | Markdown 输出              |
| `--model`             | `qwen2.5:7b`                  | 综合阶段建议至少 14B             |
| `--mcp-url`           | `http://127.0.0.1:8742/mcp`   | 覆盖 OpenChronicle MCP     |
| `--verbose`           | off                           | DEBUG 日志                 |


### 4.3 输出结构

```markdown
# 工作交接文档 · 张三
_生成时间：2026-05-09 09:30　覆盖范围：2026-04-09 ~ 2026-05-09_

> （执行摘要 1-3 句）

## 0. 接手前必须知道的事
## 一、当前负责的项目
### 项目 A
- 当前状态 / 最近决策 / 未完成任务 / 风险 / 关键联系人 / 相关文档 / 接手建议
## 二、未完成事项汇总
## 三、关键联系人
## 四、最近工作脉络
## 五、风险与阻塞
## 六、常用工具与系统
```

> **注意**：每个项目要单独调一次 LLM，再加一次综合调用。如果窗口里有 5 个项目，一次运行就有 6 次 LLM 调用，建议先测一遍 `oc-handover --days 7 --project <某一个>` 评估速度。

详见 `[handover_assistant/README.md](./handover_assistant/README.md)`。

---

## 5. 应用三：`activity_chronicler`（桌面活动长期记忆）

把 `event-*.md` sub_task 行汇总成"周报 / 月报 / 长期模式"叙事。

**典型场景：**

- "我这周到底在做什么？"——一份带时间分布的周报
- "这个月的工作主题分布"——一份月度回顾
- 长期记录工作模式：哪些是稳定的工具偏好、哪些是阶段性的项目主题
- 跨周对比：本周比上周多做了什么、少做了什么

**关键设计：**

- **时间统计完全确定性**：每条 sub_task 的 `[HH:MM-HH:MM, <app>]` 由代码解析，分钟数 / 占比 / weekday / 时段桶都不让 LLM 算，避免小模型把 312 min 写成 320 min。
- **LLM 只做两件事**：① 把 sub_tasks 聚类成有名主题（必须引证据时间段）；② 写叙事段落 + 跨周对比。
- **Observed regularity 直接复用**：OpenChronicle reducer 留下的 `Observed regularity:` 句子直接进"长期模式"字段。

### 5.1 用法

```bash
# 当前 ISO 周
python -m activity_chronicler.chronicler
# 等价于
oc-recap

# 指定 ISO 周（注意是 W18 不是 W-18）
oc-recap --week 2026-W18

# 当前自然月
oc-recap --month 2026-05

# 任意区间
oc-recap --since 2026-04-15 --until 2026-04-30

# 不做跨周对比（更快，第一次跑或没历史时用）
oc-recap --week 2026-W18 --no-compare-previous

# 复用上周的状态文件，避免重算上周
oc-recap --week 2026-W19 \
    --previous-state ./recaps/state-2026-W18.json \
    --save-state    ./recaps/state-2026-W19.json

# 模型 / 端点 / 输出 / 署名 覆盖
oc-recap --model qwen2.5:14b \
    --mcp-url http://127.0.0.1:8742/mcp \
    --owner  "赵宏波" \
    --output ./recaps/weekly.md
```

### 5.2 参数


| 参数                                             | 默认                              | 说明                               |
| ---------------------------------------------- | ------------------------------- | -------------------------------- |
| `--week YYYY-WNN`                              | 当前 ISO 周                        | ISO 周（周一 ~ 周日）                   |
| `--month YYYY-MM`                              | —                               | 自然月                              |
| `--since` / `--until`                          | —                               | 任意区间（与 `--week`/`--month` 互斥）    |
| `--compare-previous` / `--no-compare-previous` | on                              | 是否拉上一窗口做对比                       |
| `--previous-state PATH`                        | —                               | 复用上次 dump 的 JSON state，跳过重算      |
| `--save-state PATH`                            | —                               | 把本次 stats + themes 存成 JSON，下周可复用 |
| `--owner`                                      | `我`                             | 文档头部署名                           |
| `-o` / `--output`                              | `./recaps/activity-<window>.md` | Markdown 输出                      |
| `--model` / `--mcp-url` / `--verbose`          | 同上                              | 同其他两个应用                          |


### 5.3 输出片段

```markdown
# 桌面活动回顾 · 赵宏波
_窗口：ISO week 2026-W19（2026-05-04 → 2026-05-10）  生成时间：2026-05-10 22:08_

> Week of 2026-05-04 was dominated by OpenChronicle Windows-watcher
> implementation, with secondary time on Customer A renewal materials.

## 一、本窗口概览
- **可统计活动总时长**：18 小时 12 分钟（247 条 sub_task）
- **主题覆盖**：15 小时 50 分钟（约 87.0% 落入显式主题）
- **时间分布要点**：Cursor accounted for 312 of 540 tracked minutes (~58%) …

## 三、主题分布
### OpenChronicle Windows watcher
_约 5 小时 12 分钟  ·  Cursor／PowerShell_

Implemented and iterated on `win_watcher.py` … typed the commit message
"feat: dispatch WinEventHook to watcher thread" …

## 五、应用使用 Top 列表
| 应用 | 时长 (min) | 占比 |
| Cursor | 312 | 57.8% |
| Microsoft Edge | 92 | 17.0% |

## 七、长期模式（本窗口观察）
- Observed regularity: commit messages typed in Cursor's git panel during
  this window all use lowercase imperative `feat: …` / `fix: …` prefixes.

## 八、与上一窗口对比
| 类别 | 说明 |
| 新增主题 | Customer A renewal is new this week … |
| 应用占比变化 | Microsoft Edge time dropped from 220 min last week to 92 min … |

## 九、未完结线索
- An Outlook draft "renewal pricing aligned, awaiting legal sign-off" was typed Friday
  but no sent-mail sub_task followed in the window.
```

详见 `[activity_chronicler/README.md](./activity_chronicler/README.md)`。

---

## 6. 应用四：`email_task_planner`（邮件待办与日历提醒）

从 OpenChronicle 已捕获的邮件窗口、邮件网页和回复草稿中抽取待办事项与时间节点，并生成可导入日历的 `.ics` 提醒文件。

**典型场景：**

- 邮件里有人要求“请在明天下午三点前确认”
- 用户在 Outlook / Gmail / 企业邮箱里写了“我会在 5/12 前发材料”
- 每天收尾时，把邮件中的时间节点导入 Outlook / Windows Calendar

**关键边界：**

- 不接 IMAP / Microsoft Graph / Gmail API，不读取完整邮箱。
- 只分析 OpenChronicle 已捕获并写入 `event-*.md` 的邮件上下文。
- 没有明确时间节点的事项只进 Markdown，不写入 `.ics`。

### 6.1 用法

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
```

### 6.2 参数


| 参数                                    | 默认                                 | 说明                  |
| ------------------------------------- | ---------------------------------- | ------------------- |
| `--date YYYY-MM-DD`                   | —                                  | 单日                  |
| `--since` / `--until`                 | 今天/今天                              | 区间，含两端              |
| `--yesterday`                         | off                                | `--date <昨天>` 的快捷方式 |
| `-o` / `--output`                     | `./mailtasks/email-tasks-<日期>.md`  | Markdown 输出         |
| `--calendar-out`                      | `./mailtasks/email-tasks-<日期>.ics` | 日历文件输出              |
| `--remind-before`                     | `30`                               | 日历提醒提前分钟数           |
| `--default-due-time`                  | `09:00`                            | 邮件只写日期时补齐的默认时间      |
| `--model` / `--mcp-url` / `--verbose` | 同上                                 | 同其他应用               |


### 6.3 输出片段

```markdown
# 邮件待办与时间节点 · 2026-05-09

_来源：OpenChronicle event-daily entries（共 28 条；邮件候选 3 条），生成时间 2026-05-09 11:30_
_日历文件：`mailtasks/email-tasks-2026-05-09.ics`_

## 一、有时间节点的邮件待办
| 时间节点 | 负责人 | 待办 | 来源 | 置信度 |
|---|---|---|---|---|
| 2026-05-10 09:00 | me | 回复客户确认 5/12 14:00 上门时间。 | 客户现场支持时间确认 | high |
```

详见 `[email_task_planner/README.md](./email_task_planner/README.md)`。

---

## 7. 四个应用怎么搭配用

它们是多个时间维度与业务切面的视角，叠加起来是完整的"桌面工作记忆"产品：

```
       事件层              邮件时间节点             实体层                    叙事层
   ┌───────────────┐   ┌─────────────────┐   ┌───────────────┐     ┌────────────────────┐
   │ meeting/task  │   │ email_task      │   │ handover      │     │ activity_chronicler│
   │ digest        │   │ planner         │   │ assistant     │     │                    │
   │ 1 天 ~ 1 周   │   │ 1 天 ~ 1 周     │   │ 1 ~ 3 个月    │     │ 1 周 ~ N 周        │
   │ 会议 + 待办   │   │ 邮件待办 + ICS  │   │ 项目状态 +    │     │ 时间分布 + 主题 +  │
   │               │   │                 │   │ 交接          │     │ 长期模式 + 跨周对比│
   └───────┬───────┘   └────────┬────────┘   └───────┬───────┘     └─────────┬──────────┘
           │                    │                    │                       │
           └──────── 共享 ──────┴──────── 同一份 OpenChronicle 记忆 ─────────┘
```

**典型组合：**


| 场景           | 推荐组合                                                                                |
| ------------ | ----------------------------------------------------------------------------------- |
| 每天工作收尾       | `oc-digest --yesterday`                                                             |
| 每天检查邮件时间节点   | `oc-mailtasks --yesterday --calendar-out ./mailtasks/yesterday.ics`                 |
| 每周五写周报       | `oc-digest --since <周一> --until <今天>` + `oc-recap --week <本周>`                      |
| 月度回顾         | `oc-recap --month <上月>`                                                             |
| 离职 / 长假交接    | `oc-handover --days 60 --owner "张三"` + 最近 4 周的 `oc-recap` + 未完成邮件事项的 `oc-mailtasks` |
| 复盘"我这季度都在干嘛" | 连续跑 12 次 `oc-recap --week`，串起来读                                                     |


**约定：**

- 四个应用都不会写回 `~/.openchronicle/`。它们的产物落在执行目录下的 `digests/` / `handover/` / `recaps/` / `mailtasks/`，路径都可改。
- 四个应用使用同一个 LLM 实例和同一个 MCP 端点；同时跑也不会冲突，但 LLM 会排队。

---

## 8. 公共参数速查

下面这些参数四个应用**都支持**，含义一致：


| 参数                 | 来源  | 默认                                         | 说明                      |
| ------------------ | --- | ------------------------------------------ | ----------------------- |
| `--mcp-url`        | CLI | `OC_MCP_URL` 或 `http://127.0.0.1:8742/mcp` | 覆盖 OpenChronicle MCP 端点 |
| `--model`          | CLI | `OC_LLM_MODEL` 或 `qwen2.5:7b`              | 切换 Ollama 模型            |
| `--output` / `-o`  | CLI | 各自默认路径                                     | Markdown 输出文件           |
| `--verbose` / `-v` | CLI | off                                        | DEBUG 日志                |
| `OC_LLM_BASE_URL`  | env | `http://127.0.0.1:11434/v1`                | LLM 端点                  |
| `OC_LLM_API_KEY`   | env | `ollama`                                   | 占位 token，Ollama 不校验值    |


**模型选型经验**（Ollama OpenVINO 上）：


| 模型               | 适合的应用                           | 备注                           |
| ---------------- | ------------------------------- | ---------------------------- |
| `qwen2.5:7b`     | digest / recap / mailtasks      | 默认。够用，速度快                    |
| `qwen2.5:14b`    | handover / recap 综合阶段 / 邮件复杂长文本 | 长上下文综合更稳，速度慢 2–3x            |
| `qwen2.5:1.5b` 等 | 不推荐                             | JSON mode 容易输出非法格式           |
| `*-thinking-`*   | **不推荐**给本目录任何应用                 | 会大量输出 reasoning，导致 JSON 阶段超时 |


---

## 9. 故障排查（FAQ）

### 9.1 报 `LLM request failed … UNEXPECTED_EOF`

是公司代理拦截了 `127.0.0.1`。本目录的 httpx 客户端已经强制 `trust_env=False`，理论上不会出现。如果还是出现：

1. 确认你不是把 `OC_LLM_BASE_URL` 设到了一个真正的 HTTPS 地址；
2. 确认 Ollama 进程在跑：`curl http://127.0.0.1:11434/api/version`；
3. 把 `OC_LLM_BASE_URL` 显式写到 `.env`，避免读到了别的 env。

### 9.2 跑完没有 `meetings` / `themes` / `projects` / `mailtasks`

通常不是应用本身的问题，是 OpenChronicle **上游还没沉淀出对应数据**：

- `meeting_task_digest` 需要 `event-YYYY-MM-DD.md` 中已经有 sub_task 行；
- `handover_assistant` 需要 `~/.openchronicle/memory/` 中已经有 `project-*.md`；
- `activity_chronicler` 需要 `event-*.md` sub_task 行；
- `email_task_planner` 需要 `event-*.md` 中有邮件 app / 邮件网页 / 回复草稿相关 sub_task；

`project-*.md` 是 OpenChronicle classifier 阶段产出的。如果 classifier / reducer / timeline 任何一层卡住（最常见是 LLM 超时），下游就空。先看 OpenChronicle daemon 的日志，确认 timeline → reducer → classifier 都跑通了。

### 9.3 模型输出 JSON 解析失败

LLM 在 JSON mode 下偶尔会返回非法 JSON。本目录的 `LLMClient` 会把异常输出包成 `{"_raw": "...", "_error": "..."}` 而不是直接崩溃，调用方会按"空结果"处理。频繁出现时换 7B+ 的非 thinking 模型。

### 9.4 想看 LLM 真正发了什么

```bash
oc-digest --yesterday --verbose 2>&1 | tee digest.log
```

DEBUG 日志包含每次请求的 endpoint / model；如果想看 prompt 全文，直接读 `*/prompts/*.md`，那就是发出去的 system prompt。

### 9.5 PowerShell 里使用反斜杠路径

PowerShell 不需要把反斜杠转义，正常写：

```powershell
oc-recap --output .\recaps\weekly.md
```

带空格的路径用双引号包起来。

---

## 10. 共享模块与可定制点

### 10.1 共享模块（`shared/`）


| 文件                 | 职责                                                                                              |
| ------------------ | ----------------------------------------------------------------------------------------------- |
| `mcp_client.py`    | streamable-http MCP 客户端，提供 `list_memories` / `read_memory` / `search` / `recent_activity` 等异步调用 |
| `llm_client.py`    | 基于 httpx 的薄封装，直连本地 Ollama OpenVINO，支持 JSON 模式 + 健康自检                                            |
| `memory_loader.py` | 高层 API：`Entry` / `MemoryFile` dataclass + 按时间窗 / 前缀加载                                           |


### 10.2 数据流

```
┌──────────────────────┐    MCP    ┌─────────────────────────────────────┐
│  OpenChronicle       │  ◀────▶  │  example app                         │
│  daemon (127.0.0.1)  │           │  (digest / handover / chronicler /  │
│                      │           │   mailtasks)                        │
└──────────────────────┘           └─────────────────┬───────────────────┘
                                                     │ httpx → /v1/chat/completions
                                                     ▼
                                       ┌──────────────────────┐
                                       │ 本地 Ollama OpenVINO  │
                                       └─────────┬────────────┘
                                                 │
                                                 ▼
                                       ┌──────────────────────┐
                                       │  Markdown 报告        │
                                       └──────────────────────┘
```

### 10.3 可定制点


| 想改什么                                  | 改哪                                                                                        |
| ------------------------------------- | ----------------------------------------------------------------------------------------- |
| 输出 Markdown 排版                        | 各应用的 `render_markdown()`                                                                  |
| LLM 抽取规则 / 字段 / 风格                    | `*/prompts/*.md`（与 OpenChronicle 自身 prompt 风格一致）                                          |
| 时间统计的桶定义                              | `activity_chronicler/stats.py::SubTask.hour_bucket`                                       |
| 邮件待办的 `.ics` 提醒规则                     | `email_task_planner/calendar_export.py`                                                   |
| LLM 参数（temperature / max_tokens / 超时） | `shared/llm_client.py::LLMConfig`                                                         |
| 拉哪些 prefix 的 durable 文件               | 各应用 `_gather_*_files` / `_gather_durable_context`                                         |
| 增加新应用                                 | 在 `pyproject.toml.[tool.hatch.build.targets.wheel].packages` 加包 + `[project.scripts]` 加入口 |


### 10.4 设计原则

1. **只读消费**：仅通过 MCP 只读接口访问 OpenChronicle，不修改 `~/.openchronicle/` 任何内容。
2. **本地优先**：默认全程本地 LLM，无需外发任何屏幕内容。
3. **可解释**：每条产物都尽量保留来源溯源（event-daily 文件名、session id、原始时间区间、`Observed regularity:` 句子）。
4. **跨平台**：纯 Python，路径用 `pathlib`，不依赖任何 Windows / macOS 专有特性。
5. **可扩展**：抽取逻辑、prompt、输出模板都是可改的小文件。
6. **不让 LLM 算数**：`activity_chronicler` 的时间统计是确定性代码；其他应用的字段在 prompt 里也明确禁止 LLM 编造数字、人名、文件名、邮箱主题和截止时间。

