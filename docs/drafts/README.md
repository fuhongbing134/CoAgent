# 草稿、想法与废弃方案

这里集中保存尚未正式进入项目或开发计划的讨论草稿、待评估想法，以及明确废弃的方案。收录不代表批准实施，也不改变工具权限或现有规范。

当前维护依据为[文档管理规范](../../specs/DOCUMENT_MANAGEMENT_SPEC.md)。下方“本次整理依据与边界”保留 entry-2636 的迁移历史；entry-2647 复核维持现有归位，架构观察、未定提案及首期已实现措施仍按具体条目区分。

## 分类与收录清单

| 分类 | 文档 | 状态与收录理由 |
|---|---|---|
| 三端终审通过 | [工作台调用流程讨论记录](workbench-call-flow/README.md) | 保留三端独立原稿、三份互审及44项处理映射；[统一稿](../architecture/WORKBENCH_CALL_FLOW.md)已形成源码静态核对层面的共识，渲染与运行验证边界见正文；不构成新增开发计划 |
| 提案 | [README.new.md](README.new.md) | README 候选稿，形成与后续处理见[项目 README 设计](../design/PROJECT_README_DESIGN.md) |
| 已决定，已实施待检视 | [SOURCE_LAYOUT_DESIGN.md](SOURCE_LAYOUT_DESIGN.md) | §17 记录根入口决定、逐件归位及实施，§14–§16 保留候选。正式约定见[设计 §15](../design/SOURCE_LAYOUT_DESIGN.md#15-根目录与入口约定) |
| 已形成正式记录 | [源码目录重组与宿主拆分需求](SOURCE_LAYOUT_REQUIREMENTS.md) | entry-3689，0.2 同版检视基线，正式稿见 [REQ-002 1.0](../requirements/SOURCE_LAYOUT_REQUIREMENTS.md) |
| 讨论中 | [架构讨论系列](architecture-review/README.md)及[第二轮汇总](architecture-review/SUMMARY.md#第二轮复核汇总原判断修正及维持对照) | 三方署名分析、事实核查和修订历史整体保存；含已实现现状的观察，但改进建议尚未正式选定，不能当作官方架构或整体实施计划 |
| 讨论中 | [硬编码与绝对路径全量排查报告](HARDCODING_INVENTORY.md) | entry-4278；0.1；三端只读清点（workbench/tests+根配置/docs+specs）合并去重，按 A—E 优先级列出位置、原因、改进与后果；改进仅为建议、尚未授权或实施 |
| 讨论中 | [三后端skill机制与第一步方案](SKILL_MECHANISM_DISCUSSION.md) | 已汇总复盘及三端共识；通用规范1.0.0已生效；entry-2882评估暂不落地文档管理skill，保留候选、理由与重评条件，历史异议保留 |
| 讨论中 | [STAGE_DELIVERY_SPEC_DISCUSSION.md](STAGE_DELIVERY_SPEC_DISCUSSION.md) | entry-3457；0.7；草稿待优化，用户认为当前机制仍有问题；既有讨论与完整阶段需求保留，候选未定稿，正式规则未改、行为未验证 |
| 讨论中 | [TEST_RESTART_BASELINE.md](TEST_RESTART_BASELINE.md) | entry-3372／entry-3461；0.7仅追加正式需求后继关联；原0.5工具返修及18纯检查状态保留，待聚焦复查，进程/HTTP未运行 |
| 讨论中 | [通用工具授权框架](TOOL_AUTHORIZATION_DESIGN.md) | 四级分类与能力目录仍为提案，未定稿、未实施，未列入当前正式开发计划 |
| 已形成正式记录 | [BENCHMARK_RESTART_LESSONS_DISCUSSION.md](BENCHMARK_RESTART_LESSONS_DISCUSSION.md) | 0.11；两方已检视相应版本，发现项已作者修正，末次修订未另行独立复查；形成过程及本轮决定；正式教训见[../lessons/BENCHMARK_RESTART_LESSONS.md](../lessons/BENCHMARK_RESTART_LESSONS.md) |
| 已形成正式记录 | [CODE_EXAMPLE_LIBRARY_DISCUSSION.md](CODE_EXAMPLE_LIBRARY_DISCUSSION.md) | entry-3209；0.8；真实问题见§12、独立检视及处理见§13；正式代码未改，说明修订自查未复查 |
| 已形成正式记录 | [CODE_SPEC_SPLIT_DISCUSSION.md](CODE_SPEC_SPLIT_DISCUSSION.md) | entry-3189; 0.5; [CODE_WRITING_SPEC.md](../../specs/CODE_WRITING_SPEC.md) 1.0.1 / [CODE_REVIEW_SPEC.md](../../specs/CODE_REVIEW_SPEC.md) 2.0.0; 原落盘版独立检视无阻断，引用历史修订已自查未另行独立复查，效果未验证  本轮补历史表经两端聚焦复查无阻断，范围见治理记录§11 |
| 已形成正式记录 | [CODEX_GIT_PROFILE.md](CODEX_GIT_PROFILE.md) | entry-3888 启动策略及验收边界 |
| 已形成正式记录 | [CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md](CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md) | 保留 0.1 原始调查、本机验收及 §10 形成依据。正式教训见 [../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md](../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md) |
| 已形成正式记录 | [CONCURRENCY_LOAD_TEST_REQUIREMENTS.md](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md) | entry-3475；0.4；保留0.2冻结基线及形成记录；当前正文见[../requirements/CONCURRENCY_LOAD_TEST_REQUIREMENTS.md](../requirements/CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)1.1；未测试 |
| 已形成正式记录 | [CONCURRENCY_VERIFICATION_DESIGN.md](CONCURRENCY_VERIFICATION_DESIGN.md) | 形成记录0.6；§15候选1.1-C1冻结，§16两方意见及综合依据；当前正文见[../design/CONCURRENCY_VERIFICATION_DESIGN.md](../design/CONCURRENCY_VERIFICATION_DESIGN.md)1.1；综合版未另复核，隔离夹具首次诊断装配失败；§22已静态修正入口及失败观察，§23已修入口协议，单请求已创建进程并确认退出，子进程在 ready 前失败（形成记录§24），退出观察返修已通过无服务检查（形成记录§25），3551-01 提前退出观察已实测并复查，stdin 字节计数返修后 3551-02 完成一次 I2 隔离握手，待独立复查（形成记录§26） |
| 已形成正式记录 | [DESIGN_ENGINEERING_COMPLETENESS_DISCUSSION.md](DESIGN_ENGINEERING_COMPLETENESS_DISCUSSION.md) | entry-3511；形成0.2；两方候选讨论及综合处理见§8，§5候选冻结；当前规则见[../../specs/DESIGN_DECISION_RECORD_SPEC.md](../../specs/DESIGN_DECISION_RECORD_SPEC.md)1.4.0；综合版待独立复核，效果未验证 |
| 已形成正式记录 | [DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md](DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md) | entry-3234／entry-3244；0.7；文档管理1.3.2及累计33份历史整理；原31份两端初检无阻断（DeepSeek重点四份抽查，Grok结构/哈希与长单元核验），两表及G-F1返修经两端聚焦复查无阻断；本次仅状态同步、作者核对，版本边界见§11 |
| 已形成正式记录 | [PROCESS_GOVERNANCE_LESSONS_DISCUSSION.md](PROCESS_GOVERNANCE_LESSONS_DISCUSSION.md) | 保留独立主张、交叉修正及目录取舍；正式结论见[PROCESS_GOVERNANCE_LESSONS.md](../lessons/PROCESS_GOVERNANCE_LESSONS.md)，规范未修改、效果未验证 |
| 已形成正式记录 | [REQUIREMENTS_STAGE_SPEC_DISCUSSION.md](REQUIREMENTS_STAGE_SPEC_DISCUSSION.md) | [正式需求规范](../../specs/REQUIREMENTS_ANALYSIS_SPEC.md)1.0.1及description已写入；两端原稿检视完成、意见已修订，修订未另行独立复查，效果未验证 |
| 已形成正式记录 | [SPEC_DESCRIPTIONS_DISCUSSION.md](SPEC_DESCRIPTIONS_DISCUSSION.md) | entry-3161综合记录0.4；五份字段新增、一份保留，Grok落盘独立语义核对通过；索引格式及排序已修正，行为效果未验证，依据见§8  本轮补历史表经两端聚焦复查无阻断，范围见治理记录§11 |
| 阶段报告 | [CONCURRENCY_LOAD_TEST_REPORT.md](CONCURRENCY_LOAD_TEST_REPORT.md) | 0.7 历史基线，保留新版全量复跑、收尾提示回归与当时的验证边界 |
| 阶段报告 | [CONCURRENCY_LOAD_TEST_REPORT_R7.md](CONCURRENCY_LOAD_TEST_REPORT_R7.md) | 0.8 同版审阅基线，正文状态保留审阅时语境。正式稿见 [../verification/CONCURRENCY_LOAD_TEST_REPORT.md](../verification/CONCURRENCY_LOAD_TEST_REPORT.md) |
| 需求待实现／未修复 | [无输入依赖却停留受阻卡片的场景](UNNECESSARY_BLOCKED_CARD_SCENARIO.md) | entry-3260；0.1；CARD-BLOCK-001；用户报告建库讨论失败后第二轮需手动给结论才结束；保留候选调用、未知项及验收方向，未复现、未修复 |
| 待评估 | [Agent 规范遵从措施调研](AGENT_SPEC_COMPLIANCE_RESEARCH.md) | 43 项参考措施的资料集合，不是 43 项开发任务；个别措施已被采用不代表整份资料已进入计划 |
| 废弃 | [abandoned/concurrency-benchmark/README.md](abandoned/concurrency-benchmark/README.md) | entry-3310旧压测文档、工具归档入口；不作为新基线、不恢复执行 |
| 废弃 | 无独立文件的历史决定 | 放弃自动 Git 的决定保留在[正式索引的 Git 行为约束记录](../README.md)；原设计仅存于历史 feature 分支，本次不恢复或虚构文件 |

架构系列包含 README、CODEX、DEEPSEEK_HARNESS、GROK、SUMMARY 共五份文件，保留作者、议题编号、第一轮原稿和第二轮修正。正文中的旧目录名称、旧工作区状态和任务范围是当时记录，不代表当前路径；当前入口以本清单为准。

## 使用与迁出规则

entry-3189：CODE_SPEC_SPLIT_DISCUSSION.md由讨论中转为已形成正式记录0.3，用户批准和正式规范后继见主题§12；保留历轮候选，落盘待独立检视，行为效果未验证。

- 新想法先标明「待评估」或「讨论中」，记录问题、来源、候选方案及尚待决定的内容。明确放弃时标明「废弃」及原因，保留历史和后继方案链接。
- 进入正式开发计划时，写明用户决定、计划入口、范围和验收依据，将对应方案迁到正式文档区，并同时更新本清单、[设计索引](../README.md)及引用链接。实现和验证状态另记，不能将「已计划」写成「已实现」。
- 同一文件混合已实现记录和未定建议时，保留正式实施依据；可独立的想法再拆出并双向关联，不仅凭标题中的「草案」迁移整份记录。
- 不复制规范正文，不在本目录制定新的生效规范。现行规范仍以[规范索引](../../specs/README.md)为准；正式开发计划参考[BACKLOG](../BACKLOG.md)及其指向的专项计划。

## 本次整理依据与边界

用户来源：entry-2636，要求新增目录集中未正式进入项目或开发计划的文档。2026-11-10 整理基线 HEAD 为 `32b1de7b378aa46f4a43019459d649b6e6552bea`；开始时已有 docs/README.md 修改及未跟踪的架构系列，按当前内容迁移，不覆盖或回退历史。

保留在正式文档区：规范遵从设计已有首期注入实现及样例验证；期望对齐、Grok 适配、任务交接、失败可见性和 UI 简化均已有决定或实施依据。规范遵从设计内未定候选保留原上下文，并链接本目录的调研资料。根目录 P0-2、Issue8 等专项计划与验收材料已有实施/计划关联，本次不迁移。

盘点时 `docs/CAREER_ROADMAP_DESIGN.md` 已不存在，只有旧索引及架构历史提及；未能迁移，不据标题重建八周路线。若找回原稿且仍未正式入计划，再归入本目录。

适用规范：design-decision-record-spec v1.2.0（[正文](../../specs/DESIGN_DECISION_RECORD_SPEC.md)）已全文读取，遵从期望基线、状态区分、历史保留与索引维护。agent-discussion-spec v1.2.0 不适用（单 Agent 整理，无新协作讨论）；code-review-spec v1.2.0 不适用（无代码变更，已核对适用条款）。uncertain 无；此次归档未授权任何提案实施。

验收按目录、迁移清单、历史保留及引用可达性核对；符合用户集中整理目标。只调整必要文档，不改代码、测试、规范、BACKLOG、AGENTS，不执行 Git 写。三个 verify_f2 目录权限受限，未枚举其内部资料；外部链接、真实 CLI 和 Mermaid 渲染不属本次验证范围。

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-21 22:45:29 +08:00 | entry-4300／DLL 与 MXC 教训 | 复盘草稿→关联正式教训 | [调查草稿](CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md) §10，本机时钟 | 用户授权，Codex 整理 | 草稿索引状态及链接 |
| 2026-11-21 22:27:23 +08:00 | 硬编码与绝对路径排查报告 0.1 | 无→新增讨论草稿入口 | 用户要求把 entry-4278 三端排查讨论落为独立文档；本机时钟 | 用户提出；DeepSeek Harness 整理 | 草稿索引 |
| 2026-11-21 22:11:36 +08:00 | Windows DLL 故障与 MXC 复盘 0.1 | 无→新增草稿入口 | 用户要求记录定位、测试改动和配置验证。本机时钟 | 用户提出，Codex 整理 | 草稿索引 |
| 2026-11-20 11:21:44 +08:00 | entry-3821／草稿索引同步 | 原导航→归位后导航 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 路径及状态摘要 |
| 2026-11-20 08:48:28 +08:00 | entry-3754／目录草稿索引 | 已形成正式记录→补列续议待审阅 | [设计稿](SOURCE_LAYOUT_DESIGN.md) §14。本机时钟 | 用户委托，Codex 整理 | 草稿索引摘要 |
| 2026-11-20 01:01:33 +08:00 | entry-3721／设计归档 | 待检视→保留评审基线及正式后继 | [正式设计](../design/SOURCE_LAYOUT_DESIGN.md) §13。本机时钟 | 用户授权，Codex 归档 | 草稿索引 |
| 2026-11-20 00:52:04 +08:00 | entry-3721／设计草稿 0.2 | 0.1 条目→0.2 待检视 | [设计稿](SOURCE_LAYOUT_DESIGN.md) §9–§12。本机时钟 | 用户委托，Codex 修订 | 草稿索引同步 |
| 2026-11-19 01:56:13 +08:00 | entry-3668／报告归档 | 待审入口→审阅基线与正式稿关联 | [../verification/CONCURRENCY_LOAD_TEST_REPORT.md](../verification/CONCURRENCY_LOAD_TEST_REPORT.md)，本机时钟 | 用户委托，Codex 归档 | 仅状态与导航，保留审阅基线 |
| 2026-11-19 01:51:11 +08:00 | entry-3668／报告 0.8 | 无→新增 r7 报告待审入口 | [CONCURRENCY_LOAD_TEST_REPORT_R7.md](CONCURRENCY_LOAD_TEST_REPORT_R7.md)，本机时钟 | 用户委托，Codex 整理 | 草稿导航，正式归档待同版检视 |
| 2026-11-18 13:38:52 +08:00 | 0.7／entry-3631 | 0.6→返修及复跑 | 同版检视反馈与本机实测 | Codex | 阶段报告入口同步 |
| 2026-11-18 13:20:22+08:00 | entry-3631／报告 0.6 | 0.5→0.6 待独立检视 | entry-3631 实测，本机时钟 | Codex | 报告索引同步 |
| 2026-11-18 12:16:04 +08:00 | entry-3603／报告 0.5 | 0.4→0.5 待独立复查 | [阶段报告](CONCURRENCY_LOAD_TEST_REPORT.md)，本机时钟 | Codex | 阶段报告索引同步 |
| 2026-11-18 03:05:13 +08:00 | entry-3571／报告收尾 | 待独立检视→阶段报告入口 | 两份独立检视；拟同步报告标题、版本说明和交付状态；本机时钟 | Codex | 草稿索引与报告 |
| 2026-11-18 02:56:09 +08:00 | entry-3571／阶段报告入口 | 无→报告草稿入口 | [阶段报告](CONCURRENCY_LOAD_TEST_REPORT.md)；本机时钟 | 用户提出；Codex 起草 | 草稿导航，待独立检视 |
| 2026-11-18 02:29:31+08:00 | entry-3551／stdin 字节统计 | 编码失败→单请求 I2 握手完成 | 形成记录§26，fixture-smoke-3551-02；Python本机时钟 | Codex | 测试夹具及状态同步，待独立复查 |
| 2026-11-18 02:17:55+08:00 | entry-3551／退出观察 | 待返修→无服务检查通过 | 形成记录§25及DS-R1/R2、G-R1/R2；Python本机时钟 | Codex | 测试夹具返修，待新根单请求及独立复查 |
| 2026-11-18 02:04:55+08:00 | entry-3543／单请求诊断 | 待运行→已创建并确认退出，未到达 ready | 形成记录§24及隔离运行证据 | Codex | 单请求失败，待正式检视 |
| 2026-11-18 02:02:17+08:00 | entry-3543／受控入口协议 | 协议待修→已修，待单请求验证 | 形成记录§23、入口解析器内存检查 | Codex | 测试夹具与活动状态，待正式检视 |
| 2026-11-17 14:36:12+08:00 | entry-3527／最小返修 | 装配失败待修→静态修正、协议阻断待处理 | 形成记录§22；Python本机时钟 | Codex | 仅测试侧入口和失败观察，未重跑、未正式检视 |
| 2026-11-17 14:29:52+08:00 | entry-3527／首次运行 | 静态待验证→装配失败待修 | 形成记录§21；Python本机时钟 | Codex | 未创建受控进程，代码未改 |
| 2026-11-17 14:26:23+08:00 | entry-3527／运行前修正 | 导入及清理待查→定向核查与测试侧修正完成 | 形成记录§20；Python本机时钟 | Codex | 未运行、未正式检视；首次闭环待执行 |
| 2026-11-17 14:21:47+08:00 | entry-3527／单请求监督 | 部分接线→监督驱动已编码 | 形成记录§19；Python本机时钟 | Codex | 仅静态检查，运行前导入副作用待查、未正式检视 |
| 2026-11-17 14:13:18+08:00 | entry-3527／夹具接线 | 未编码表述→部分实现 | 用户实施授权；关联形成记录§18；Python本机时钟 | Codex | 范围验收不变；未运行、未正式检视 |
| 2026-11-17 13:25:34 +08:00 | entry-3511／规范补充正式化 | 讨论中候选→已形成正式记录0.2 | [形成记录](DESIGN_ENGINEERING_COMPLETENESS_DISCUSSION.md)§8；Python本机时钟 | Codex综合 | 设计规范1.4.0；综合版待独立复核，效果未验证 |
| 2026-11-17 13:13:53 +08:00 | entry-3511／工程完整性候选入口 | 无→新增讨论候选入口 | [前置记录](DESIGN_ENGINEERING_COMPLETENESS_DISCUSSION.md)§1；PowerShell本机时钟 | 用户授权；Codex | 仅草稿入口；正式规范未改，待两方讨论 |
| 2026-11-17 12:57:19 +08:00 | entry-3499／完整性综合入口 | 1.0/候选待讨论→正式1.1及冻结形成记录 | [前置记录](CONCURRENCY_VERIFICATION_DESIGN.md)§16；Python本机时钟 | Codex综合 | 仅文档入口；综合版未另复核、未实施验证 |
| 2026-11-17 12:42:38 +08:00 | entry-3499／候选入口 | 形成记录0.4→0.5候选待讨论 | [前置记录](CONCURRENCY_VERIFICATION_DESIGN.md)§14—§15；Python本机时钟 | 用户要求；Codex | 当前正式稿仍1.0，未实施验证 |
| 2026-11-17 12:29:43 +08:00 | entry-3495／设计正式化 | 讨论中→已形成正式记录 | [前置记录](CONCURRENCY_VERIFICATION_DESIGN.md)§13；Python本机时钟 | 用户授权；Codex | 本主题移组并关联正式1.0，冻结基线保留 |
| 2026-11-17 12:24:21 +08:00 | entry-3479／设计交接 | 无入口→新增0.3草稿入口 | [前置记录](CONCURRENCY_VERIFICATION_DESIGN.md)§12；Python本机时钟 | 负责人交接授权；Codex维护 | 仅本条及历史；0.2复核无阻断，未实施验证 |
| 2026-11-17 11:48:37 +08:00 | entry-3475／结构整理 | 原摘要→同步分析1.1及形成记录 | 用户要求；关联草稿§17；Python本机时钟 | 用户；Codex | 仅文档导航，未测试 |
| 2026-11-17 11:30:55 +08:00 | entry-3471／正式化入口 | 讨论中→已形成正式记录 | [形成记录](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)§16；Python本机时钟 | 用户授权；Codex | 同步正式1.0、草稿0.3、测试稿0.7，仅文档 |
| 2026-11-17 11:22:44 +08:00 | entry-3461／需求综合0.2 | 初稿待质疑→意见综合待审阅 | [需求稿](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)§13—§15；Python本机时钟 | 两方分析；Codex建议 | 仅需求及索引，范围收窄，未执行测试 |
| 2026-11-17 11:03:26 +08:00 | entry-3461／需求分析0.1 | 无→新增需求初稿及测试稿关联 | [需求初稿](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)；测试稿§19；Python本机时钟 | 用户要求；Codex | 仅草稿及索引，部分接纳建议未批准，未测试 |
| 2026-11-17 10:50:48 +08:00 | entry-3457／待优化标注0.7 | 综合建议→草稿待优化 | 用户要求；[主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§30；Python本机时钟 | 用户；Codex | 仅草稿与索引状态，保留历史，未实施正式规则 |
| 2026-11-17 01:30:01 +08:00 | entry-3445／遵从综合0.6 | 候选待质疑→两方回应及当前建议 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§26—§29；Python本机时钟 | 两方讨论；Codex综合 | 仅草稿与索引，效果未验证 |
| 2026-11-17 01:19:17 +08:00 | entry-3445／遵从分析0.5 | 组织讨论→实际履行与审核候选 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§19—§25；Python本机时钟 | 用户澄清；Codex准备 | 仅草稿与索引，待独立质疑 |
| 2026-11-17 01:08:22 +08:00 | entry-3431／综合0.4 | 候选质疑→当前推荐A待用户审阅 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§16—§18；Python本机时钟 | 两方讨论；Codex综合 | 仅草稿与索引，唯一性未验收 |
| 2026-11-17 00:49:39 +08:00 | entry-3431／唯一性分析0.3 | 组织推荐→职责边界及description候选入口 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§10—§15；Python本机时钟 | 用户；Codex | 仅草稿与索引；新议题待独立讨论 |
| 2026-11-17 00:44:53 +08:00 | entry-3417／讨论综合0.2 | 候选待讨论→推荐B待用户审阅 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§7—§9；Python本机时钟 | 两方讨论；Codex整理 | 仅草稿与入口；范围及文件粒度仍为候选 |
| 2026-11-17 00:27:14 +08:00 | entry-3417／讨论准备 | 无→阶段规范落点候选入口 | [主题草稿](STAGE_DELIVERY_SPEC_DISCUSSION.md)§1—§6；Python本机时钟 | 用户；Codex | 仅草稿与索引，待两方独立意见 |
| 2026-11-16T14:09:12+08:00 | entry-3372／0.5 | 工具需修改→集中返修待复查 | [主题稿](TEST_RESTART_BASELINE.md)§17—§19；Python本机时钟 | Codex | 18纯检查，未运行集成 |
| 2026-11-16 13:54:06+08:00 | entry-3372／0.4 | 设计查证→工具作者实现待检视 | [前置记录](TEST_RESTART_BASELINE.md)§15及交接§16；Python本机时钟 | Codex | 仅入口更新，集成未运行 |
| 2026-11-16 13:42:28+08:00 | entry-3372／0.3 | 0.2方案→修订待审核 | [前置记录](TEST_RESTART_BASELINE.md)§13及§14；Python本机时钟 | Codex | 仅索引；0.2检视边界保留，未执行 |
| 2026-11-16 13:33:02 +08:00 | entry-3372／0.2方案 | 目标待核→并发性能新方案待审核 | 用户澄清；[前置记录](TEST_RESTART_BASELINE.md)§6；Python本机时钟 | Codex | 仅当前索引，旧时点状态保留；未执行 |
| 2026-11-16 13:28:50 +08:00 | entry-3372／重启测试基线 | 无→新增非废案测试草稿入口 | 用户要求；[前置草稿](TEST_RESTART_BASELINE.md)§5；Python本机时钟 | Codex | 仅索引，测试未执行、范围待核对 |
| 2026-11-16 13:13:25 +08:00 | entry-3353／G-N1与G-N2收尾 | 两方发现项→作者修正并明确版本范围 | Grok有效检视3f4a86ec1e854356b1c5776a37054e9e；[处理记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | Codex按负责人决定处理 | 仅文档；末次修订未另行独立复查，行为效果未验证 |
| 2026-11-16 13:03:01 +08:00 | entry-3353／N1返修 | 检视发现漏项→删除并补查 | DeepSeek调用685c355e87914e79b46f0664684c5bf0；[返修记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | Codex按负责人决定修正 | 作者检查，Grok检视尚待；效果未验证 |
| 2026-11-16 12:56:49 +08:00 | entry-3353／文档清理与正式化 | 旧表述→删除指定提及并关联正式记录 | 用户要求；[前置记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | 用户授权；Codex实施 | 仅文档；作者静态检查，两方检视待完成 |
| 2026-11-16 12:47:22 +08:00 | entry-3327／0.8状态同步 | 返修后待复核→记录0.7复核及0.8范围 | [主题记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§12；Python本机时钟 | Codex | 仅当前摘要；0.8未另行独立复核，旧历史语境保留 |
| 2026-11-16 12:24:58 +08:00 | entry-3327／验收返修 | 首轮清理→补语义及交付链接检查 | 主题草稿§12；Python本机时钟 | Codex | 作者静态检查；返修版本待独立复核 |
| 2026-11-16 12:11:23 +08:00 | entry-3327／主题状态同步 | 提醒及隐含检查→明确触发与可核查义务 | 用户要求；[前置记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§12；Python本机时钟 | 用户授权；Codex实施 | 仅本主题摘要，完整清理及独立复核待完成；作者检查，待独立复核，行为效果未验证 |
| 2026-11-16 11:43:35+08:00 | entry-3310／收尾 | 待回应及核验→0.4状态同步 | [收尾依据](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§10—§11；Python本机时钟 | Codex | 仅本主题摘要；0.3核验无阻断，本次未另行独立核验 |
| 2026-11-16 11:31:41 +08:00 | entry-3310／讨论综合（本轮据主题稿§6重建摘要） | 候选→三方综合供审阅 | [主题形成记录](BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§6 | Codex综合 | 当时0.3，限定及独立核验待回应；本轮误替换历史行后据主题记录恢复语义 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／旧压测废案 | 活动预检入口→废案入口；新增本轮教训讨论 | entry-3310用户归档授权；前置留稿BENCHMARK_RESTART_LESSONS_DISCUSSION.md；Python本机时钟 | 用户；Codex实施 | 仅归档及引用同步，不恢复测试；教训待讨论 |
| 2026-11-16 10:46:34 +08:00 | entry-3284／预检（留稿0.2） | 预检待执行→无模型预检通过 | 本轮日志与audit；Python本机时钟 | Codex | 仅预检，完整验收未完成 |
| 2026-11-15 23:58:34 +08:00 | entry-3260／故障需求登记 | 无入口→CARD-BLOCK-001待实现草稿入口 | 用户要求及场景草稿§1—§6；Python本机时钟 | 用户；Codex记录 | 新增独立状态分组，既有条目相对顺序保留 |
| 2026-11-15 23:50:33 +08:00 | entry-3244／状态收尾 | 旧待复查摘要→治理0.7及两端完成状态 | 治理记录§10—§11；G-N1；Python本机时钟 | Codex | 当前索引摘要；原条目顺序和旧历史保留 |
| 2026-11-15 23:36:41 +08:00 | entry-3244／补表返修 | 旧摘要→两主题补表版本及待复查状态 | 治理草稿§8 | Codex | 条目原顺序保留 |
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-15 23:14:52 +08:00 | entry-3234／返修同步 | 旧摘要→主题0.8及规范1.3.1返修记录 | [返修依据](DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md)§4 | Codex | 索引版本及检视边界 |
| 2026-11-15 23:07:40 +08:00 | entry-3234／入口同步 | 原摘要→文档治理修订入口及案例版本同步 | [修订依据](DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md) | 用户；Codex | 当前索引；旧历史保留 |
| 2026-11-15 22:21:33 +08:00起记录 | entry-3209／首批案例 | 0.1讨论中→0.2已形成正式记录 | 用户批准及重试语境见主题§8，实施见§9 | 用户；Codex实施 | 移入正式记录组按文件名排序，链接正式案例入口，待两端独立检视 |
| 2026-11-15 22:09:26 +08:00 | entry-3199／案例库讨论 | 无记录→0.1讨论建议已整理 | 用户建议、DeepSeek独立评价及负责人修正见[CODE_EXAMPLE_LIBRARY_DISCUSSION.md](CODE_EXAMPLE_LIBRARY_DISCUSSION.md) | 用户原文；DeepSeek评价；Codex整理 | 讨论中组按文件名插入；仅保存讨论，正式建库未实施、效果未验证 |
| 2026-11-15 21:56:42 +08:00 | entry-3189／实施及检视补记 | 0.2讨论→0.3实施→0.4检视处理 | 用户批准拆分、Grok独立检视无阻断；四项处理见[主题记录](CODE_SPEC_SPLIT_DISCUSSION.md)§12—§13 | 用户授权；Grok检视；Codex维护 | 已形成正式记录组，保留排序；修订后自查，未另行独立复查，效果未验证 |
| 2026-11-15 21:38:40 +08:00 | entry-3175／综合建议 | 0.1独立候选→0.2待交叉确认及用户审阅 | 两端评估、Grok交叉及逐项处理见[主题记录](CODE_SPEC_SPLIT_DISCUSSION.md)§8—§10 | Codex综合 | 仅本主题摘要及历史，分组排序保留，正式规范未改 |
| 2026-11-15 21:22:20 +08:00 | entry-3175／拆分讨论准备 | 无入口→0.1候选待两端讨论 | 用户要求先讨论边界校验与分支必要性、审阅后才正式拆分，见[讨论草稿](CODE_SPEC_SPLIT_DISCUSSION.md) | 用户原文；Codex准备 | 讨论中组内按文件名新增入口；正式规范未改 |
| 2026-11-15 21:11:34 +08:00 | entry-3161／入口及收尾 | 20:54:15创建0.1入口、21:05:20实施更新0.2→0.3正式记录 | 准备、实施及Grok落盘复查调用见[主题记录](SPEC_DESCRIPTIONS_DISCUSSION.md)§8 | 用户授权；Grok复查；Codex整理 | 补记实际依据；主题移入正式记录组并按文件名排序，行为效果未验证 |
| 2026-11-15 20:07:51 +08:00 | entry-3127／检视修订 | 原稿待检视→两端意见已处理 | 草稿0.8保存逐项处理、差异及验证限制 | 两端检视；Codex修订 | 本主题摘要，分组排序保留 |
| 2026-11-15 19:57:43 +08:00 | entry-3127／正式规范 | 讨论中→已形成正式记录 | 用户批准需求规范及description，正式稿和必要迁移已写入待检视 | 用户决定；Codex实施 | 本主题移入正式记录组，组内PROCESS后REQUIREMENTS，原组序保留 |
| 2026-11-15 19:48:43 +08:00 | entry-3114／条目收口 | 待交叉→六章建议供用户审阅 | 草稿0.6保留观点变化及收口依据，未改生效规范 | 两端交叉；Codex处理 | 本主题摘要，原分组结构保留 |
| 2026-11-15 19:41:41 +08:00 | entry-3114／条款综合 | 独立评估待进行→综合条款待交叉回应 | 两端意见及逐项处理已落盘，修订未冒称一致 | 两端独立评估；Codex综合 | 本主题摘要，组序与结构不变 |
| 2026-11-15 19:32:54 +08:00 | entry-3114／条目准备 | 承载收口→具体条目候选待评估 | 用户限定先讨论需求规范内容，description新增后置 | 用户原文；Codex准备 | 本主题摘要，组序和索引结构不变 |
| 2026-11-15 19:27:18 +08:00 | entry-3101／讨论收口 | 待交叉回应→交叉处理及推荐收口 | 草稿0.3保留观点改变，落实H-R与G-R4；不等于用户实施决定 | 两端交叉；Codex处理 | 本主题摘要，保留原分组与排序 |
| 2026-11-15 19:20:41 +08:00 | entry-3101／综合候选 | 待独立评估→综合候选待交叉回应 | 两端完整主张与Codex处理落盘，承载及描述仍未生效 | 两端评估；Codex综合 | 本主题状态摘要 |
| 2026-11-15 19:14:38 +08:00 | entry-3101／需求阶段讨论 | 无入口→新增讨论草稿入口 | 用户要求多端讨论需求理解澄清阶段承载与规范描述，准备候选待评估 | 用户原文；Codex整理 | 讨论中组内按文件名字母序插入，其他组保留 |
| 2026-11-15 19:02:52 +08:00 | entry-3085／文档收口 | 本主题行拆开讨论中分组→移至该组之后，复核发现项已处理 | Grok F8指出分组不连续，按§1.4修正且不重排其他组 | Grok复核；Codex处理 | 本主题索引行位置和状态 |
| 2026-11-15 18:53:06 +08:00 | entry-3085／正式化 | 正式化准备→正式记录已创建、待复核 | 两端目录评估接受，正式稿已落盘 | Codex整理 | 本主题摘要及正式稿链接 |
| 2026-11-15 18:48:16 +08:00 | entry-3085／流程教训 | 讨论收口、待边界裁决→正式化准备 | 用户已决定日志承接、合入职责和历史位置，目录仍待评估 | 用户原文；Codex同步 | 仅流程教训草稿索引行；既有归档说明保留 |
