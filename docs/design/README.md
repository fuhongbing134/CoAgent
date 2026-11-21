# docs/design/ — 正式设计文档

本目录存放已进入正式项目或开发计划的设计文档，按 [design-decision-record-spec](../../specs/DESIGN_DECISION_RECORD_SPEC.md) 维护；分类、状态、引用与历史同时遵循[文档管理规范](../../specs/DOCUMENT_MANAGEMENT_SPEC.md)。摘要按正文已有证据记录，具体验证范围以对应记录为准。总导航见[文档索引](../README.md)。

| 主题 | 文档 | 现状 |
|---|---|---|
| 规范遵从设计与参考调研 | [AGENT_SPEC_COMPLIANCE_DESIGN.md](AGENT_SPEC_COMPLIANCE_DESIGN.md)；[调研资料](../drafts/AGENT_SPEC_COMPLIANCE_RESEARCH.md) | 设计保留已实现的注入及验证记录；未选型的措施集合移入草稿区 |
| 协作任务交接与计划接收 | [COLLABORATION_DISPATCH_DESIGN.md](COLLABORATION_DISPATCH_DESIGN.md) | 方案已实现（normalize_owner_plan 归一化），三组回归通过；真实环境增量追加已验证一条路径，最终收尾另有协议失败，详见正文 |
| 并发正确性验证 | [CONCURRENCY_VERIFICATION_DESIGN.md](CONCURRENCY_VERIFICATION_DESIGN.md) | 1.1；S1—S4工程说明补齐；候选已两方讨论，综合版未另复核，部分编码，首次诊断装配失败；入口及失败观察已静态修正，入口协议已修（形成记录§23），单请求已创建进程并确认退出，子进程在 ready 前失败（形成记录§24），退出观察返修已通过无服务检查（形成记录§25），3551-01 提前退出观察已实测并复查，stdin 字节计数返修后 3551-02 完成一次 I2 隔离握手，待独立复查（形成记录§26） |
| 代码变更与现状图文同步 | [DIAGRAM_SYNC_RULES_DESIGN.md](DIAGRAM_SYNC_RULES_DESIGN.md) | 已编写，规范条款见 code-review-spec 1.3.0 §13.1，已合入 main |
| 用户期望对齐规范 | [EXPECTATION_ALIGNMENT_DESIGN.md](EXPECTATION_ALIGNMENT_DESIGN.md) | 两份规范补齐期望基线、逐步核对和纠偏，实施与检查结果见正文，待非作者审核 |
| 失败可见性 | [FAILURE_VISIBILITY_DESIGN.md](FAILURE_VISIBILITY_DESIGN.md) | 方向 A 已实现，专项回归通过；第 8 节保留当时全套 165 通过/1 失败/1 跳过；前端失败后续处置见 UI 设计第 8 节，完整真实环境验收及合入状态待补证据 |
| Grok 适配修正 | [GROK_ADAPTATION_DESIGN.md](GROK_ADAPTATION_DESIGN.md) | entry-2105 修正已通过 Grok 独立复查，R1 关闭，R2/R3 及已核对 P2 维持关闭；真实 CLI 验收与合入状态另待证据 |
| 项目公开README与上手路径 | [PROJECT_README_DESIGN.md](PROJECT_README_DESIGN.md) | entry-3028价值、工程边界与测试导航优化及两端复核修订完成，静态检查完成；此前隔离启动受DLL错误阻碍，运行上手仍未验收 |
| 源码目录重组与 Web 宿主拆分 | [SOURCE_LAYOUT_DESIGN.md](SOURCE_LAYOUT_DESIGN.md) | 1.0 正式设计。实施与受控验收见 §14，根入口决定及文档归位见 §15 |
| 工作台 UI 简化 | [UI_SIMPLIFICATION_DESIGN.md](UI_SIMPLIFICATION_DESIGN.md) | 已实施，用户报告回归通过并确认小卡片、任务区与目录面板移除；窄屏及其余浏览器交互验收待完成，证据见第 8 节 |

旧压测材料已按entry-3310归入废案：[../drafts/abandoned/concurrency-benchmark/README.md](../drafts/abandoned/concurrency-benchmark/README.md)。不属于本目录当前执行计划，历史验证限制继续保留。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-20 11:21:44 +08:00 | entry-3821／设计索引同步 | 原导航→归位后导航 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 路径及状态摘要 |
| 2026-11-20 10:52:16 +08:00 | entry-3803／源码目录设计索引 | 现行入口摘要→增列已确认常设根文件 | [正式设计](SOURCE_LAYOUT_DESIGN.md) §15。本机时钟 | Codex 同步 | 设计索引摘要 |
| 2026-11-20 09:35:36 +08:00 | entry-3771／源码目录设计索引 | 送审候选摘要→现行设计与入口约定 | [正式设计](SOURCE_LAYOUT_DESIGN.md) §14–§15。本机时钟 | 用户授权，Codex 整理 | 设计索引摘要 |
| 2026-11-20 09:22:41 +08:00 | entry-3762／设计导航 | 增补 §15 续议入口 | 正式设计 §15，本机时钟 | 用户授权，Codex 整理 | 设计索引 |
| 2026-11-20 01:01:33 +08:00 | entry-3721／源码目录重组 1.0 | 无→正式设计入口 | [正式设计](SOURCE_LAYOUT_DESIGN.md) §13。本机时钟 | 用户授权，Codex 归档 | 设计索引，实施待开展 |
| 2026-11-18 02:29:31+08:00 | entry-3551／stdin 字节统计 | 编码失败→单请求 I2 握手完成 | 形成记录§26，fixture-smoke-3551-02；Python本机时钟 | Codex | 测试夹具及状态同步，待独立复查 |
| 2026-11-18 02:17:55+08:00 | entry-3551／退出观察 | 待返修→无服务检查通过 | 形成记录§25及DS-R1/R2、G-R1/R2；Python本机时钟 | Codex | 测试夹具返修，待新根单请求及独立复查 |
| 2026-11-18 02:04:55+08:00 | entry-3543／单请求诊断 | 待运行→已创建并确认退出，未到达 ready | 形成记录§24及隔离运行证据 | Codex | 单请求失败，待正式检视 |
| 2026-11-18 02:02:17+08:00 | entry-3543／受控入口协议 | 协议待修→已修，待单请求验证 | 形成记录§23、入口解析器内存检查 | Codex | 测试夹具与活动状态，待正式检视 |
| 2026-11-17 14:36:12+08:00 | entry-3527／最小返修 | 装配失败待修→静态修正、协议阻断待处理 | 形成记录§22；Python本机时钟 | Codex | 仅测试侧入口和失败观察，未重跑、未正式检视 |
| 2026-11-17 14:29:52+08:00 | entry-3527／首次运行 | 静态待验证→装配失败待修 | 形成记录§21；Python本机时钟 | Codex | 未创建受控进程，代码未改 |
| 2026-11-17 14:26:23+08:00 | entry-3527／运行前修正 | 导入及清理待查→定向核查与测试侧修正完成 | 形成记录§20；Python本机时钟 | Codex | 未运行、未正式检视；首次闭环待执行 |
| 2026-11-17 14:21:47+08:00 | entry-3527／单请求监督 | 部分接线→监督驱动已编码 | 形成记录§19；Python本机时钟 | Codex | 仅静态检查，运行前导入副作用待查、未正式检视 |
| 2026-11-17 14:13:18+08:00 | entry-3527／夹具接线 | 未编码表述→部分实现 | 用户实施授权；关联形成记录§18；Python本机时钟 | Codex | 范围验收不变；未运行、未正式检视 |
| 2026-11-17 12:57:19 +08:00 | entry-3499／完整性综合入口 | 1.0/候选待讨论→正式1.1及冻结形成记录 | [前置记录](../drafts/CONCURRENCY_VERIFICATION_DESIGN.md)§16；Python本机时钟 | Codex综合 | 仅文档入口；综合版未另复核、未实施验证 |
| 2026-11-17 12:29:43 +08:00 | entry-3495／正式设计入口 | 无→S1—S4设计1.0 | [前置记录](../drafts/CONCURRENCY_VERIFICATION_DESIGN.md)§13；Python本机时钟 | 用户授权；Codex | 仅文档导航，未实施验证 |
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／旧压测废案 | 两条活动压测入口→移出正式设计清单并指向废案 | entry-3310用户归档授权；前置留稿BENCHMARK_RESTART_LESSONS_DISCUSSION.md；Python本机时钟 | 用户；Codex实施 | 仅归档及引用同步，不恢复测试；教训待讨论 |
| 2026-11-16 10:46:34 +08:00 | entry-3284／预检（留稿0.2） | 预检待执行→无模型预检通过 | 本轮日志与audit；Python本机时钟 | Codex | 仅当时预检，完整验收未完成；entry-3310已废案，不是当前计划 |
