# docs/ — 设计文档索引

本目录存放方案/技术决策的设计文档，按 [design-decision-record-spec](../specs/DESIGN_DECISION_RECORD_SPEC.md) 维护。索引按「主题 → 文档路径 → 一句话现状」定位，同主题连续演进优先更新已有文档，较大的独立方案再拆分并互相引用。

| 主题 | 文档路径 | 现状 |
|---|---|---|
| Grok 适配修正 | [GROK_ADAPTATION_DESIGN.md](GROK_ADAPTATION_DESIGN.md) | 权限、终态、会话及三Agent测试修正，待非作者检视与真实验收 |
| 协作任务交接与计划接收 | [COLLABORATION_DISPATCH_DESIGN.md](COLLABORATION_DISPATCH_DESIGN.md) | 方案已实现（normalize_owner_plan 归一化），三组回归通过；真实环境增量追加已验证一条路径，最终收尾另有协议失败，详见正文 |
| 失败可见性 | [FAILURE_VISIBILITY_DESIGN.md](FAILURE_VISIBILITY_DESIGN.md) | 已实现：协作回复暂存 + 接收后同事务确认终态（方向A）；本地回归 165 通过/1 前端既有失败/1 跳过，真实环境消息与 job 内失败可见性已验证，已合入 main |
| 通用工具授权 | [TOOL_AUTHORIZATION_DESIGN.md](TOOL_AUTHORIZATION_DESIGN.md) | 草案待详细讨论；四级分类与能力目录提案，未实施、未改变权限 |
| 最小 Git 自动化 | [GIT_COMMIT_SERVICE_DESIGN.md](GIT_COMMIT_SERVICE_DESIGN.md) | 共用模块已提交；双操作 stdio MCP 适配已实现待审核，宿主配置、真实权限及客户端验收待完成；锁/worktree 留后 |
| 工作台 UI 简化 | [UI_SIMPLIFICATION_DESIGN.md](UI_SIMPLIFICATION_DESIGN.md) | 设计已决定，本轮实施中；精简 Agent 与目录界面、任务迁侧栏，回归及浏览器验收待完成 |

本索引不代表目录中其他既有设计或研究文档已迁移或完成新规范核查。本次未增加 docs 索引的自动注入。

更新注记（2026-10-28 19:49:15 +08:00）：原现状为“定位结论已确认，修复方案待设计”；依据实施结果及 entry-1879 只读核验更新。增量任务完成不等于整个 job 正常收尾。
