# docs/design/ — 正式设计文档

本目录存放已进入正式项目或开发计划的设计文档，按 [design-decision-record-spec](../../specs/DESIGN_DECISION_RECORD_SPEC.md) 维护；分类、状态、引用与历史同时遵循[文档管理规范](../../specs/DOCUMENT_MANAGEMENT_SPEC.md)。摘要按正文已有证据记录，具体验证范围以对应记录为准。总导航见[文档索引](../README.md)。

| 主题 | 文档 | 现状 |
|---|---|---|
| 规范遵从设计与参考调研 | [AGENT_SPEC_COMPLIANCE_DESIGN.md](AGENT_SPEC_COMPLIANCE_DESIGN.md)；[调研资料](../drafts/AGENT_SPEC_COMPLIANCE_RESEARCH.md) | 设计保留已实现的注入及验证记录；未选型的措施集合移入草稿区 |
| 协作任务交接与计划接收 | [COLLABORATION_DISPATCH_DESIGN.md](COLLABORATION_DISPATCH_DESIGN.md) | 方案已实现（normalize_owner_plan 归一化），三组回归通过；真实环境增量追加已验证一条路径，最终收尾另有协议失败，详见正文 |
| 代码变更与现状图文同步 | [DIAGRAM_SYNC_RULES_DESIGN.md](DIAGRAM_SYNC_RULES_DESIGN.md) | 已编写，规范条款见 code-review-spec 1.3.0 §13.1，已合入 main |
| 用户期望对齐规范 | [EXPECTATION_ALIGNMENT_DESIGN.md](EXPECTATION_ALIGNMENT_DESIGN.md) | 两份规范补齐期望基线、逐步核对和纠偏，实施与检查结果见正文，待非作者审核 |
| 失败可见性 | [FAILURE_VISIBILITY_DESIGN.md](FAILURE_VISIBILITY_DESIGN.md) | 方向 A 已实现，专项回归通过；第 8 节保留当时全套 165 通过/1 失败/1 跳过；前端失败后续处置见 UI 设计第 8 节，完整真实环境验收及合入状态待补证据 |
| Grok 适配修正 | [GROK_ADAPTATION_DESIGN.md](GROK_ADAPTATION_DESIGN.md) | entry-2105 修正已通过 Grok 独立复查，R1 关闭，R2/R3 及已核对 P2 维持关闭；真实 CLI 验收与合入状态另待证据 |
| 项目公开README与上手路径 | [PROJECT_README_DESIGN.md](PROJECT_README_DESIGN.md) | entry-3028价值、工程边界与测试导航优化及两端复核修订完成，静态检查完成；此前隔离启动受DLL错误阻碍，运行上手仍未验收 |
| 工作台 UI 简化 | [UI_SIMPLIFICATION_DESIGN.md](UI_SIMPLIFICATION_DESIGN.md) | 已实施，用户报告回归通过并确认小卡片、任务区与目录面板移除；窄屏及其余浏览器交互验收待完成，证据见第 8 节 |

旧压测材料已按entry-3310归入废案：[../drafts/abandoned/concurrency-benchmark/README.md](../drafts/abandoned/concurrency-benchmark/README.md)。不属于本目录当前执行计划，历史验证限制继续保留。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／旧压测废案 | 两条活动压测入口→移出正式设计清单并指向废案 | entry-3310用户归档授权；前置留稿BENCHMARK_RESTART_LESSONS_DISCUSSION.md；Python本机时钟 | 用户；Codex实施 | 仅归档及引用同步，不恢复测试；教训待讨论 |
| 2026-11-16 10:46:34 +08:00 | entry-3284／预检（留稿0.2） | 预检待执行→无模型预检通过 | 本轮日志与audit；Python本机时钟 | Codex | 仅当时预检，完整验收未完成；entry-3310已废案，不是当前计划 |
