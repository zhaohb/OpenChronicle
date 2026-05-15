# 员工交接助手（handover_assistant）

把 OpenChronicle 长期沉淀的 `project-*.md` / `person-*.md` / `org-*.md` / `tool-*.md` 与最近若干周的 event-daily 综合起来，自动生成一份"员工离职/休假/转岗"交接文档。

## 解决什么问题

企业里，交接文档之所以难写，是因为：

- 项目状态散落在 IM、邮件、CRM、文档、ERP 等多个系统里，写交接时要逐一回忆
- 关键联系人和决策原因没有人专门记录
- 临时阻塞、风险点、未完成事项最容易被漏掉
- 写完之后，接手人还得靠老员工口头补全细节

OpenChronicle 已经长期记录了这些桌面行为；本 example 在不暴露原始截图的前提下，把它们组装成一份初稿。

## 用法

```bash
# 默认最近 30 天，全部活动项目
python -m handover_assistant.handover

# 指定窗口
python -m handover_assistant.handover --since 2026-04-01 --until 2026-05-09

# 指定一个项目
python -m handover_assistant.handover --project openchronicle

# 自定义标题署名
python -m handover_assistant.handover --owner "张三"

# 自定义输出
python -m handover_assistant.handover --output ./out/handover.md

# 切换本地 Ollama OpenVINO 上的另一个模型
python -m handover_assistant.handover --model qwen2.5:14b
```

> 默认连接 `http://127.0.0.1:11434/v1`，模型 `qwen2.5:7b`。交接文档生成涉及多次 LLM 调用（每个项目一次 + 一次综合），建议本地至少使用 7B+ 模型；若速度允许，14B 的总结质量更稳。

或使用安装后的脚本入口：

```bash
oc-handover --since 2026-04-01 --owner "张三"
```

## 输出结构

```markdown
# 工作交接文档 · 张三
_生成时间：2026-05-09 09:30　覆盖范围：2026-04-09 ~ 2026-05-09_

> （执行摘要：3 句话）

## 0. 接手前必须知道的事
- 客户 A 续约谈判截至 5/20，决策人是张总（不是采购李经理）
- 生产数据库备份脚本只在我的本地任务计划里，需要迁到 server-ops

## 一、当前负责的项目
### 项目 A
- 当前状态 / 最近决策 / 未完成任务 / 风险 / 关键联系人 / 相关文档 / 接手建议

## 二、未完成事项汇总
| 项目 | 负责人 | 任务 | 截止 |

## 三、关键联系人
| 姓名 | 角色 | 关注点 |

## 四、最近工作脉络
（按日期 timeline，可展开原始条目）

## 五、风险与阻塞
| 范围 | 风险 | 缓解措施 |

## 六、常用工具与系统
| 名称 | 用途 | 接入说明 |
```

## 工作原理

```
list_memories(prefix="project-")
        │
        ▼
  for each project ──→ read_memory ──→ LLM Pass A (project_status) ──┐
                                                                     │
recent_activity (event-*) ───────────────────────────────────────────┤
                                                                     │
list_memories(prefix="tool-/person-/org-") ──→ read_memory ──────────┤
                                                                     ▼
                                    LLM Pass B (handover_doc — 综合执行摘要)
                                                                     │
                                                                     ▼
                                                        render_markdown → .md
```

两阶段调用的好处：

- 每个项目独立调一次，保证细节不被截断或互相串扰
- 第二阶段在已结构化的项目摘要上做综合，避免一次性吞掉过长上下文

## 关键文件

| 文件 | 作用 |
|---|---|
| `handover.py` | CLI 入口 |
| `builder.py` | 项目摘要 + 综合摘要 + Markdown 渲染 |
| `prompts/project_status.md` | 单项目摘要 prompt |
| `prompts/handover_doc.md` | 综合交接 prompt |

## 注意

- 输出始终是**初稿**，建议人工再核对一遍敏感数据再交付。
- 该工具只读 OpenChronicle 数据，不修改。如需补充未被捕获的信息，直接编辑生成的 Markdown 即可。
- 项目识别基于 `project-*.md` 文件的存在与更新时间，未被分类器写入的"工作"不会出现。如果发现某个项目缺失，可以让 OpenChronicle 多跑一段时间，或者手动新建 `project-<name>.md`。
