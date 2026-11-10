# docs/ — 设计文档索引

本目录存放方案/技术决策的设计文档，按 [design-decision-record-spec](../specs/DESIGN_DECISION_RECORD_SPEC.md) 维护。索引按「主题 → 文档路径 → 一句话现状」定位，同主题连续演进优先更新已有文档，较大的独立方案再拆分并互相引用。

文档分类、状态、引用与历史维护同时遵循[文档管理规范](../specs/DOCUMENT_MANAGEMENT_SPEC.md)。以下摘要按正文已有证据维护，具体验证范围以对应记录为准。

| 主题 | 文档路径 | 现状 |
|---|---|---|
| 草稿、讨论与想法 | [drafts/README.md](drafts/README.md) | 未入正式计划的提案、调研及架构讨论集中于此；废弃方案保留状态和原因 |
| 求职作品优化路线 | 原路径 `CAREER_ROADMAP_DESIGN.md`（文件缺失） | 旧索引记录为八周核心、八周补强草案；本次未找到原稿，未迁移或重建，找回后按实际状态归类 |
| 用户期望对齐规范 | [EXPECTATION_ALIGNMENT_DESIGN.md](EXPECTATION_ALIGNMENT_DESIGN.md) | 两份规范补齐期望基线、逐步核对和纠偏，实施与检查结果见正文，待非作者审核 |
| Grok 适配修正 | [GROK_ADAPTATION_DESIGN.md](GROK_ADAPTATION_DESIGN.md) | entry-2105 修正已通过 Grok 独立复查，R1 关闭，R2/R3 及已核对 P2 维持关闭；真实 CLI 验收与合入状态另待证据 |
| 协作任务交接与计划接收 | [COLLABORATION_DISPATCH_DESIGN.md](COLLABORATION_DISPATCH_DESIGN.md) | 方案已实现（normalize_owner_plan 归一化），三组回归通过；真实环境增量追加已验证一条路径，最终收尾另有协议失败，详见正文 |
| 失败可见性 | [FAILURE_VISIBILITY_DESIGN.md](FAILURE_VISIBILITY_DESIGN.md) | 方向 A 已实现，专项回归通过；第 8 节保留当时全套 165 通过/1 失败/1 跳过；前端失败后续处置见 UI 设计第 8 节，完整真实环境验收及合入状态待补证据 |
| 通用工具授权 | [drafts/TOOL_AUTHORIZATION_DESIGN.md](drafts/TOOL_AUTHORIZATION_DESIGN.md) | 草案待详细讨论；四级分类与能力目录提案，未实施、未改变权限 |
| 规范遵从设计与参考调研 | [AGENT_SPEC_COMPLIANCE_DESIGN.md](AGENT_SPEC_COMPLIANCE_DESIGN.md)；[调研资料](drafts/AGENT_SPEC_COMPLIANCE_RESEARCH.md) | 设计保留已实现的注入及验证记录；未选型的措施集合移入草稿区 |
| 架构分析与复核 | [drafts/architecture-review/README.md](drafts/architecture-review/README.md) | 三方原稿、修订和汇总整体归档；现状观察可供参考，改进建议不等于正式开发计划 |
| 架构现状与改进方向 | [ARCHITECTURE.md](ARCHITECTURE.md) | 官方现状图已定稿；改进方向为提案未实施；adapter execute 已收敛，普通文件退路延后，共享适配唯一性保留分歧待裁决 |
| Git 行为约束 | 本条记录（entry-2582） | 已决定放弃代码自动 Git，改用 spec 约束 Git 行为；教训是实施前先验证 Agent 原生能力，再决定是否需要额外代码。历史设计保留于原 feature 分支 |
| 工作台 UI 简化 | [UI_SIMPLIFICATION_DESIGN.md](UI_SIMPLIFICATION_DESIGN.md) | 已实施，用户报告回归通过并确认小卡片、任务区与目录面板移除；窄屏及其余浏览器交互验收待完成，证据见第 8 节 |

本索引不代表目录中其他既有设计或研究文档已迁移或完成新规范核查。本次未增加 docs 索引的自动注入。

更新注记（2026-10-28 19:49:15 +08:00）：原现状为“定位结论已确认，修复方案待设计”；依据实施结果及 entry-1879 只读核验更新。增量任务完成不等于整个 job 正常收尾。

整理注记（2026-11-10，entry-2647）。依据 Grok、UI 正文同步已完成的审核与实施摘要。失败可见性原索引称真实环境已验证、已合入 main，正文尚未提供相应完整记录，本次收紧摘要并保留待补证据项。历史测试结果继续按各自时点阅读。
