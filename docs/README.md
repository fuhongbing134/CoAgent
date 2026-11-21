# docs/ — 文档索引

本目录存放正式需求分析、方案/技术决策的设计文档、架构文档、代码判断案例、草稿讨论、正式流程教训和验证报告，按 [design-decision-record-spec](../specs/DESIGN_DECISION_RECORD_SPEC.md) 与 [document-management-spec](../specs/DOCUMENT_MANAGEMENT_SPEC.md) 维护。索引按「分区 → 入口 → 一句话说明」定位，各分区内的文件级清单与现状见对应子目录 README。

| 分区 | 入口 | 说明 |
|---|---|---|
| 历史归档 | [archive/README.md](archive/README.md) | 协作协议、Issue #8 和 P0-2 专题历史 |
| 项目待办 | [BACKLOG.md](BACKLOG.md) | 持续维护的后续需求与执行状态 |
| 架构文档 | [architecture/README.md](architecture/README.md) | 架构现状记录、改进方向与调用流程统一稿 |
| 代码判断案例 | [code-examples/README.md](code-examples/README.md) | 入口0.6；好坏对照及两项真实问题，含收录时间与未落地建议；真实案例0.4独立检视无阻断，本次说明自查未复查 |
| 设计文档 | [design/README.md](design/README.md) | 已进入正式计划的方案/技术设计，文件级清单与现状见分区内 |
| 草稿与讨论 | [drafts/README.md](drafts/README.md) | 未入正式计划的提案、调研、讨论与废弃方案；旧压测见[drafts/abandoned/concurrency-benchmark/README.md](drafts/abandoned/concurrency-benchmark/README.md) |
| 操作指南 | [guides/README.md](guides/README.md) | MCP 接入和 Web 协作操作 |
| 流程教训 | [lessons/README.md](lessons/README.md) | 正式流程／协作教训；事故复盘仍见根目录[postmortem/P0-2_POSTMORTEM.md](../postmortem/P0-2_POSTMORTEM.md)，本轮不迁移旧材料 |
| 需求分析 | [requirements/README.md](requirements/README.md) | 首份正式需求分析REQ-001／1.1；范围及验证状态见目录内 |
| 验证报告 | [verification/README.md](verification/README.md) | 已归档的受控验证报告，实测范围及未验证项见报告 |
| Git 行为约束 | 本条记录（entry-2582） | 已决定放弃代码自动 Git，改用 spec 约束 Git 行为；教训是实施前先验证 Agent 原生能力，再决定是否需要额外代码。历史设计保留于原 feature 分支 |

本索引不代表目录中其他既有设计或研究文档已迁移或完成新规范核查。本次未增加 docs 索引的自动注入。

更新注记（2026-10-28 19:49:15 +08:00）：原现状为“定位结论已确认，修复方案待设计”；依据实施结果及 entry-1879 只读核验更新。增量任务完成不等于整个 job 正常收尾。

整理注记（2026-11-10，entry-2647）。依据 Grok、UI 正文同步已完成的审核与实施摘要。失败可见性原索引称真实环境已验证、已合入 main，正文尚未提供相应完整记录，本次收紧摘要并保留待补证据项。历史测试结果继续按各自时点阅读。

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-20 11:21:44 +08:00 | entry-3821／总索引同步 | 原导航→归位后导航 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 路径及状态摘要 |
| 2026-11-19 01:56:13 +08:00 | entry-3668／验证报告入口 | 无→新增验证报告分区 | [审阅基线](drafts/CONCURRENCY_LOAD_TEST_REPORT_R7.md)，本机时钟 | 用户委托，Codex 归档 | 文档导航 |
| 2026-11-17 11:48:37 +08:00 | entry-3475／结构整理 | 原摘要→同步分析1.1及形成记录 | 用户要求；关联草稿§17；Python本机时钟 | 用户；Codex | 仅文档导航，未测试 |
| 2026-11-17 11:30:55 +08:00 | entry-3471／需求分析分区 | 无→独立正式需求入口 | 用户要求；[前置记录](drafts/CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)§16；Python本机时钟 | 用户授权；Codex | 仅文档导航及正式化 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／旧压测废案 | 草稿分区摘要→补充旧压测废案入口 | entry-3310用户归档授权；前置留稿BENCHMARK_RESTART_LESSONS_DISCUSSION.md；Python本机时钟 | 用户；Codex实施 | 仅归档及引用同步，不恢复测试；教训待讨论 |
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-15 23:07:40 +08:00 | entry-3234／入口同步 | 原摘要→文档治理修订入口及案例版本同步 | [修订依据](drafts/DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md) | 用户；Codex | 当前索引；旧历史保留 |
| 2026-11-15 22:21:33 +08:00起记录 | entry-3209／首批代码案例 | 用户批准→三主题及入口已建立 | 依据[主题记录](drafts/CODE_EXAMPLE_LIBRARY_DISCUSSION.md)§8—§9 | 用户；Codex实施 | 新分区按路径排序，待两端独立检视、使用效果未验证 |
| 2026-11-15 18:53:06 +08:00 | entry-3085／流程教训入口 | 无正式流程教训分区→新增lessons入口 | 用户授权、两端目录评估接受；区别于已有事故复盘 | 用户；Codex整理 | 增加分区及范围说明，旧注记和Git记录保留 |
