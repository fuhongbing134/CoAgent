# docs/ — 文档索引

本目录存放方案/技术决策的设计文档、架构文档与草稿讨论，按 [design-decision-record-spec](../specs/DESIGN_DECISION_RECORD_SPEC.md) 与 [document-management-spec](../specs/DOCUMENT_MANAGEMENT_SPEC.md) 维护。索引按「分区 → 入口 → 一句话说明」定位，各分区内的文件级清单与现状见对应子目录 README。

| 分区 | 入口 | 说明 |
|---|---|---|
| 设计文档 | [design/README.md](design/README.md) | 已进入正式计划的方案/技术设计，文件级清单与现状见分区内 |
| 架构文档 | [architecture/README.md](architecture/README.md) | 架构现状记录、改进方向与调用流程统一稿 |
| 草稿与讨论 | [drafts/README.md](drafts/README.md) | 未入正式计划的提案、调研、讨论与废弃方案归档 |
| Git 行为约束 | 本条记录（entry-2582） | 已决定放弃代码自动 Git，改用 spec 约束 Git 行为；教训是实施前先验证 Agent 原生能力，再决定是否需要额外代码。历史设计保留于原 feature 分支 |

本索引不代表目录中其他既有设计或研究文档已迁移或完成新规范核查。本次未增加 docs 索引的自动注入。

更新注记（2026-10-28 19:49:15 +08:00）：原现状为“定位结论已确认，修复方案待设计”；依据实施结果及 entry-1879 只读核验更新。增量任务完成不等于整个 job 正常收尾。

整理注记（2026-11-10，entry-2647）。依据 Grok、UI 正文同步已完成的审核与实施摘要。失败可见性原索引称真实环境已验证、已合入 main，正文尚未提供相应完整记录，本次收紧摘要并保留待补证据项。历史测试结果继续按各自时点阅读。
