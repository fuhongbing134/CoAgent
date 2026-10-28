# docs/ — 设计文档索引

本目录存放方案/技术决策的设计文档，按 [design-decision-record-spec](../specs/DESIGN_DECISION_RECORD_SPEC.md) 维护。索引按「主题 → 文档路径 → 一句话现状」定位，同主题连续演进优先更新已有文档，较大的独立方案再拆分并互相引用。

| 主题 | 文档路径 | 现状 |
|---|---|---|
| 协作任务交接与计划接收 | [COLLABORATION_DISPATCH_DESIGN.md](COLLABORATION_DISPATCH_DESIGN.md) | 方案已实现（normalize_owner_plan 归一化），三组回归通过；真实环境增量追加已验证一条路径，最终收尾另有协议失败，详见正文 |
| 失败可见性 | [FAILURE_VISIBILITY_DESIGN.md](FAILURE_VISIBILITY_DESIGN.md) | 草案：协作回复暂存与接收后确认终态，方向已收敛待用户确认；未实施，卡片可见性待运行时验证 |

本索引首期仅登记上述主题，不代表目录中其他既有设计或研究文档已迁移或完成新规范核查。本次未增加 docs 索引的自动注入。

更新注记（2026-10-28 19:49:15 +08:00）：原现状为“定位结论已确认，修复方案待设计”；依据实施结果及 entry-1879 只读核验更新。增量任务完成不等于整个 job 正常收尾。
