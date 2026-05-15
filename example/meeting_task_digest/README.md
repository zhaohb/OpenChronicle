# 会议与任务自动沉淀（meeting_task_digest）

把 OpenChronicle 已经沉淀的 `event-YYYY-MM-DD.md` 二次加工成"今日/本周会议与待办报告"。

## 解决什么问题

Windows 用户每天在 Teams、Zoom、飞书、腾讯会议、微信等多种应用之间切换：

- 会议记录散落在不同会议软件、聊天窗口和文档里
- 待办事项藏在邮件、IM 草稿、文档评论里，很难统一收集
- 一周下来要写周报时，常常想不起来"我做了什么决定"

OpenChronicle 已经把这些桌面行为压缩成了结构化记忆；本 example 把它们进一步加工成可读的报告。

## 用法

```bash
# 今天的会议与任务
python -m meeting_task_digest.digest

# 指定日期
python -m meeting_task_digest.digest --date 2026-05-08

# 指定区间（适合写周报）
python -m meeting_task_digest.digest --since 2026-05-04 --until 2026-05-10

# 昨天
python -m meeting_task_digest.digest --yesterday

# 指定输出路径
python -m meeting_task_digest.digest --since 2026-05-04 --until 2026-05-10 \
    -o ./out/week-report.md

# 切换本地 Ollama OpenVINO 上的另一个模型
python -m meeting_task_digest.digest --model qwen2.5:14b
```

> 默认连接 `http://127.0.0.1:11434/v1`，模型 `qwen2.5:7b`。如需改端点或模型，编辑 `example/.env` 中的 `OC_LLM_BASE_URL` / `OC_LLM_MODEL`。

也可以用安装后的脚本入口：

```bash
oc-digest --yesterday
```

## 输出示例

```markdown
# 会议与任务沉淀 · 2026-05-08

_来源：OpenChronicle event-daily entries（共 12 条），生成时间 2026-05-09 09:30_

## 一、会议汇总

| 日期 | 时间 | 主题 | 应用 | 参与人 |
|---|---|---|---|---|
| 2026-05-08 | 10:00-10:45 | Q3 路线图同步 | Microsoft Teams | Alice, Bob, 我 |
| 2026-05-08 | 14:00-14:30 | 客户 A 续约沟通 | 腾讯会议 | 客户 A 张总, 我 |

### 📌 Q3 路线图同步
- **时间**：2026-05-08 10:00-10:45
- **应用**：Microsoft Teams
- **参与人**：Alice, Bob, 我
- **来源 session**：`sess_4a2f...`

**关键决策：**
- 把"统一登录"功能从 Q3 推迟到 Q4
- Alice 接手 metrics 模块的重构

**待办事项：**
- [me] 周五前给出登录改动的影响面评估
- [Alice] 5/15 前提交 metrics 重构方案

## 二、独立待办

| 负责人 | 任务 | 截止 | 上下文 | 置信度 |
|---|---|---|---|---|
| me | 回邮件确认 5/12 上门时间 | 2026-05-10 | Outlook 邮件草稿 | high |
```

## 工作原理

```
event-2026-05-08.md   ──┐
event-2026-05-09.md   ──┼─→  LLM Pass 1 (meeting_extract.md) ─→ meetings + tasks
                        │
                        └─→  LLM Pass 2 (task_extract.md)    ─→ extra standalone tasks

                                          ↓ 合并去重

                              render_markdown → 输出 .md
```

两遍调用是为了：

- 第一遍专注会议结构化（高准确率）
- 第二遍兜底捞起会议外的零散待办（覆盖率）

## 关键文件

| 文件 | 作用 |
|---|---|
| `digest.py` | CLI 入口，解析参数，调用核心逻辑 |
| `extractor.py` | 拉取 event-daily、调用 LLM、聚合结果 |
| `prompts/meeting_extract.md` | 会议抽取 prompt |
| `prompts/task_extract.md` | 任务兜底抽取 prompt |
