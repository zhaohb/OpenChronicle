# activity_chronicler · 桌面活动的长期记忆

> 把 OpenChronicle 已经在本地沉淀的 `event-YYYY-MM-DD.md` / `project-*.md` / `tool-*.md` /
> `Observed regularity:` 等记忆素材，组装成 **周报 / 月报 / 长期模式** 形式的
> 长期叙事。"我这周到底在干嘛"、"这个月时间花在哪了"、"和上周比有什么变化" 都在这里。

---

## 1. 解决什么问题

OpenChronicle 抓的是"现在"——每一段窗口、每一个 sub_task、每一条 capture。

但回头看时，人想要的是"**叙事**"：

- 这一周我主要在做哪些**主题**？各占多少时间？
- 我的工作节奏是怎样的（早上 / 下午 / 晚上各占多少）？
- 我有没有**重复的工作模式 / 工具偏好**（沉淀进 `Observed regularity:`）？
- 跟**上一周比**，我多做了什么、少做了什么？
- 哪些**线头是开着的**（典型：写到一半的草稿、提到了但没收尾的项目）？

`activity_chronicler` 把这些问题用一份 Markdown 一次性回答。

它跟同目录的另外两个 example 是互补的：

| App | 时间窗 | 重点 | 主驱动 |
|---|---|---|---|
| `meeting_task_digest` | 1 天 ~ 1 周 | 会议 + 待办（事件层） | LLM 抽取 |
| `handover_assistant` | 1 - 3 个月 | 项目状态 + 交接（实体层） | LLM 综合 |
| **`activity_chronicler`** | **1 周 ~ N 周** | **行为分布 + 主题 + 习惯**（叙事层） | **统计 + LLM** |

---

## 2. 设计原则（与另两个 example 一致）

1. **只读消费 OpenChronicle**：通过 MCP（`http://127.0.0.1:8742/mcp`）拉数据，**不写回**
   `~/.openchronicle/memory/` —— 那是 OpenChronicle 自己的领地。本应用的输出落在
   `./recaps/` 下。
2. **本地优先**：LLM 默认走 Ollama OpenVINO 的 `/v1/chat/completions`，与父
   README 的 LLM Backend 设置一致。
3. **统计与 LLM 解耦 / 数据可追溯**：
   - 时间分布、应用占比、weekday / 时段桶 — 全部由代码确定性计算（解析 sub_task 行
     的 `[HH:MM-HH:MM, <app>]`），**不让 LLM 算数**。
   - LLM 只做两件事：① 把 sub_tasks **聚类**成主题；② 写**叙事**段落。
   - prompt 强制每个主题、每个 regularity 必须能引到具体证据。
4. **跟 OpenChronicle 的 prompt 风格对齐**：verbatim preservation、authorship guard、
   anti-hallucination、`Observed regularity:` 关键句直接复用。

---

## 3. 输入与产物

**输入（OpenChronicle 提供）：**

| 来源 | 经由 MCP 工具 | 用途 |
|---|---|---|
| `event-YYYY-MM-DD.md` 中所有 entries | `read_memory(path="event-…")` | 主输入：解析 sub_task 行 |
| `project-*.md` / `topic-*.md` / `tool-*.md` / `user-*.md` | `list_memories` + `read_memory` | 主题命名提示 + durable context |
| `Observed regularity:` 句（从 sub_task entries 内摘） | 同上 | "长期模式" 字段的最强信号 |

**输出：** `./recaps/activity-week-2026-W19.md`（或月份/区间命名）—— 一份结构化
Markdown，本身就是可以放入 OpenChronicle 之外的"长期记忆系统"的输入。

可选：用 `--save-state` 把本次运行的统计 + 主题 dump 成 JSON，下周再用
`--previous-state` 直接读，省去对历史窗口的重算。

---

## 4. 安装

跟同目录其它 example 共用一份 `pyproject.toml` 和 `.env`。

```bash
cd C:\hongbo\UX\OpenChronicle\example
python -m pip install -e .
copy .env.example .env  # 然后按 LLM Backend 章节调整
```

确保 OpenChronicle daemon 已在跑：

```bash
cd C:\hongbo\UX\OpenChronicle
openchronicle start --foreground
# 另一终端：
openchronicle status
```

---

## 5. 使用

```bash
# 当前 ISO 周
python -m activity_chronicler.chronicler

# 指定 ISO 周
python -m activity_chronicler.chronicler --week 2026-W18

# 当前月
python -m activity_chronicler.chronicler --month 2026-05

# 任意区间
python -m activity_chronicler.chronicler --since 2026-04-15 --until 2026-04-30

# 不做跨周对比（更快）
python -m activity_chronicler.chronicler --week 2026-W18 --no-compare-previous

# 复用上一次跑的状态做对比，避免重算上周
python -m activity_chronicler.chronicler --week 2026-W19 \
    --previous-state ./recaps/state-2026-W18.json \
    --save-state    ./recaps/state-2026-W19.json

# 模型 / 端点覆盖
python -m activity_chronicler.chronicler --model qwen2.5:14b \
    --mcp-url http://127.0.0.1:8742/mcp \
    --output  ./recaps/weekly.md
```

控制台例子：

```text
Recap written to recaps\activity-week-2026-W19.md
```

---

## 6. 产物结构（节选）

```markdown
# 桌面活动回顾 · 我

_窗口：ISO week 2026-W19（2026-05-04 → 2026-05-10）  生成时间：2026-05-10 22:08_

> Week of 2026-05-04 was dominated by OpenChronicle Windows-watcher
> implementation, with secondary time on Customer A renewal materials.

## 一、本窗口概览
- **可统计活动总时长**：18 小时 12 分钟（247 条 sub_task）
- **主题覆盖**：15 小时 50 分钟（约 87.0% 落入显式主题）
- **时间分布要点**：Cursor accounted for 312 of 540 tracked minutes (~58%)…

## 三、主题分布
### OpenChronicle Windows watcher
_约 5 小时 12 分钟  ·  Cursor／PowerShell_

Implemented and iterated on `win_watcher.py` … typed the commit message
"feat: dispatch WinEventHook to watcher thread" …

<details><summary>证据时间段</summary>
- `2026-05-04 09:12-10:30, Cursor`
- `2026-05-04 14:02-15:48, Cursor`
…
</details>

## 五、应用使用 Top 列表
| 应用 | 时长 (min) | 占比 |
|---|---|---|
| Cursor | 312 | 57.8% |
| Microsoft Edge | 92 | 17.0% |
| Outlook | 68 | 12.6% |
…

## 七、长期模式（本窗口观察）
- Observed regularity: commit messages typed in Cursor's git panel during
  this window all use lowercase imperative `feat: …` / `fix: …` prefixes (3 commits).

## 八、与上一窗口对比
| 类别 | 说明 |
|---|---|
| 新增主题 | Customer A renewal is new this week; no Outlook activity on that thread last week. |
| 应用占比变化 | Microsoft Edge time dropped from 220 min last week to 90 min this week … |

## 九、未完结线索
- An Outlook draft "renewal pricing aligned, awaiting legal sign-off" was typed Friday
  but no sent-mail sub_task followed in the window.
```

---

## 7. 内部数据流

```
                  ┌────────────────────────────────────┐
                  │  OpenChronicle MCP (read-only)     │
                  │   list_memories / read_memory      │
                  └─────────────┬──────────────────────┘
                                │ event-*.md entries
                                ▼
                ┌────────────────────────────────────┐
                │ stats.parse_event_entries          │ ← 确定性
                │   解析  [HH:MM-HH:MM, <app>] …     │   regex
                │ stats.compute_stats                │
                │   总分钟 / by_app / by_weekday /   │
                │   by_hour_bucket / by_day          │
                └─────────────┬──────────────────────┘
                              │ ActivityStats + sub_tasks
                              ▼
       ┌──────────────────────────────────────────────┐
       │ synthesizer (LLM × 2)                        │
       │  ① theme_cluster.md  — 聚类主题             │
       │  ② weekly_recap.md   — 写叙事 + 跨周对比    │
       │  附加：从 entries 中拽 `Observed regularity:`│
       └─────────────┬────────────────────────────────┘
                     │ Recap
                     ▼
              chronicler.render_markdown
                     │
                     ▼
        ./recaps/activity-week-…md  +  state-…json
```

---

## 8. 与"OpenChronicle classifier" 的边界

OpenChronicle 自带一个 classifier，会把 `user-`/`tool-`/`topic-` 等长期事实写入
`~/.openchronicle/memory/`。**本应用不重写那批文件**，它只是把 classifier 已经
沉淀好的内容（再加一层从 `event-*.md` 实时解析出的统计）合成"窗口级叙事"——
classifier 关心的是"是不是 durable fact"，本应用关心的是"这一周 / 这一月在做什么"。

这两层是叠加关系：classifier 沉淀的事实越多，`activity_chronicler` 的产物里
"长期模式"和主题命名就越准。
