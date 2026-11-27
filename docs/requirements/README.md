# 需求分析

| 字段 | 值 |
|---|---|
| 版本 | 1.6 |
| 状态 | 正式文档目录 |
| 来源 | entry-3471用户要求建立单独需求分析文件夹；[形成记录](../drafts/CONCURRENCY_LOAD_TEST_REQUIREMENTS.md#16-正式化前置记录entry-3471) |

本目录存放正式需求分析，描述背景、目标、范围、接纳理由及验收；方案设计、实施和测试结果分别依据各自记录。目录收录不代表测试已执行或能力已验证。

复制 [REQUIREMENTS_ANALYSIS_TEMPLATE.md](REQUIREMENTS_ANALYSIS_TEMPLATE.md) 可起草需求分析正文。当前为 0.2，由 0.1 独立检视后的建议修订。

跨阶段统一状态记录另置于 [`tracking/`](tracking/README.md)，每条需求一份；状态推进和阶段证据规则见[任务执行规范§7](../../specs/TASK_EXECUTION_SPEC.md#7-需求阶段导航与统一状态)。本目录需求文档仍是需求定义与基线的权威来源。

| 文档 | 编号／版本 | 当前结论 |
|---|---|---|
| [COLLABORATION_ROUTING_REQUIREMENTS.md](COLLABORATION_ROUTING_REQUIREMENTS.md) | REQ-004／1.5 | U1 统一 3 分钟及到期收尾（含恰好截止）、U2 首次失败后最多两次重试（每次计入本轮调用预算）和文件变更安全门已更新；无回退、三态回报与条件重试已确认，能力和产品验收待验证 |
| [CONCURRENCY_LOAD_TEST_REQUIREMENTS.md](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md) | REQ-001／1.1 | 并发正确性、隔离与恢复部分接纳；负载及长期增长暂不纳入，保留重评；未执行测试 |
| [DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md](DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md) | REQ-003／1.0 | 六项已接纳，主要验收已完成。见[正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md) |
| [SOURCE_LAYOUT_REQUIREMENTS.md](SOURCE_LAYOUT_REQUIREMENTS.md) | REQ-002／1.0 | 源码与测试分目录、源码按功能分级、拆分宿主职责。实施与验收待开展 |

上层入口：[../README.md](../README.md)。后续需求文档按文件名忽略大小写排序；新主题先按项目文档规范保存形成草稿。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-27 21:09:36 +08:00 | entry-4986／授权截止端点 | REQ-004 1.4→1.5 | 用户更新 d17779af；本机时钟 | 用户裁决，Codex 记录 | U1/A4 到期收尾及设计引用 |
| 2026-11-27 19:00:03 +08:00 | entry-4920／用户确认无回退 | REQ-004 1.3→1.4 | 用户原文与[讨论稿](../drafts/COLLABORATION_ROUTING_DESIGN_DISCUSSION.md) §4.31 | 用户决定，Codex 记录 | 更新 U2/R3/A3，关闭回退相关待决项 |
| 2026-11-27 18:54:56 +08:00 | entry-4913／统一授权等待 | REQ-004 1.2→1.3 | 用户原文「统一三分钟吧」及[讨论稿](../drafts/COLLABORATION_ROUTING_DESIGN_DISCUSSION.md) §4.30 | 用户决定，Codex 记录 | U1/R4/A4 统一三分钟，无单任务例外，产品待验 |
| 2026-11-25 22:19:18 +08:00 | entry-4750／预算修订 | REQ-004 1.1→1.2、设计草稿 0.2→0.3 | entry-4733 用户更正；本机时钟为落盘时间 | 用户决定；Codex 记录 | 每次重试计入本轮调用预算；期限覆盖及回退权限仍待决定，产品验收待验证 |
| 2026-11-25 21:27:51 +08:00 | 1.5／entry-4727 | REQ-004 1.0→1.1；同步需求入口摘要 | 用户原文及[正式需求](COLLABORATION_ROUTING_REQUIREMENTS.md)；前置记录见[形成草稿](../drafts/COLLABORATION_ROUTING_REQUIREMENTS.md) entry-4727 | 用户决定；Codex 记录 | 同步 U1/U2 已决定项及未决边界 |
| 2026-11-25 20:18:13 +08:00 | entry-4687／状态追踪入口 | 无→新增 `tracking/` 导航 | 用户授权；[讨论草稿](../drafts/DESIGN_TO_IMPLEMENTATION_PROCESS_DISCUSSION.md)§10；PowerShell本机时钟 | 用户授权；Codex实施 | 仅添加独立状态追踪入口；未推断或预填现有需求状态 |
| 2026-11-25 08:06:14+08:00 | 1.4／entry-4620 | 新增正式需求 REQ-004 | 用户决定及[正式需求](COLLABORATION_ROUTING_REQUIREMENTS.md)，本机时钟 | 用户授权，Codex 执行 | 需求索引 |
| 2026-11-24 19:28:26 +08:00 | REQ-003／收尾状态同步 | 待实施验收→主要验收已完成 | [正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md)。本机时钟 | 用户确认收尾，Codex 整理 | 需求索引 |
| 2026-11-23 08:08:00 +08:00 | 1.3／entry-4358 | 新增正式需求 REQ-003 | 三方交叉检视一致，用户确认正式落地；本机时钟 | 用户确认；DeepSeek Harness 转正 | 需求索引 |
| 2026-11-19 23:58:56 +08:00 | 1.2／entry-3689 | 新增正式需求 REQ-002 | 两位审阅者同版通过，见正式稿 §8 | Codex 归档 | 需求索引 |
| 2026-11-17 11:48:37 +08:00 | entry-3475／结构整理 | 原摘要→同步分析1.1及形成记录 | 用户要求；关联草稿§17；Python本机时钟 | 用户；Codex | 仅文档导航，未测试 |
| 2026-11-17 11:30:55 +08:00 | entry-3471／1.0 | 无→正式需求分析目录 | 用户要求；关联草稿§16；Python本机时钟 | 用户授权；Codex整理 | 新目录及首份正式分析，未启动实施 |
