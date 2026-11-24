# 流程与协作教训

本分区保存已选定、供持续参考的流程与协作教训。正式记录表示结论已整理归档；对应规范是否修改、措施是否实施及效果是否验证，分别以正文证据为准。当前生效规则仍以[规范索引](../../specs/README.md)为入口。

讨论过程保留在[草稿索引](../drafts/README.md)所指原稿，与正式记录双向关联。事故复盘已有根目录[postmortem/P0-2_POSTMORTEM.md](../../postmortem/P0-2_POSTMORTEM.md)，本轮只建立可达链接，不迁移或重新核定其历史结论。流程教训与事故复盘长期是否合并，待材料增多且出现实际分类成本时再评估，本轮不处理。

## 正式记录

| 文档 | 状态与说明 |
|---|---|
| [BENCHMARK_RESTART_LESSONS.md](BENCHMARK_RESTART_LESSONS.md) | 正式记录1.0.1；五项教训；两方已检视相应版本，发现项已作者修正，末次修订未另行独立复查，行为效果未验证 |
| [PROCESS_GOVERNANCE_LESSONS.md](PROCESS_GOVERNANCE_LESSONS.md) | 文档草稿追溯、编码与检视职责、需求分析及失败交接的正式教训记录；独立复核发现项已修正收口，规范未修改，效果未验证 |
| [WINDOWS_VENV_DLL_MXC_LESSONS.md](WINDOWS_VENV_DLL_MXC_LESSONS.md) | 正式记录 1.0。涵盖本机 MXC 验收、同链对照、宿主取证及已完成主要验收的六项改进。见[正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md) |

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-24 19:28:26 +08:00 | DLL/MXC 教训／后续验收关联 | 六项后续验证预告→关联正式验收 | [正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md)。本机时钟 | 用户确认收尾，Codex 整理 | 教训索引 |
| 2026-11-21 22:45:29 +08:00 | entry-4300／DLL 与 MXC 教训 | 无→新增正式教训入口 | [调查草稿](../drafts/CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md) §10，本机时钟 | 用户授权，Codex 整理 | 正式教训索引 |
| 2026-11-16 13:13:25 +08:00 | entry-3353／G-N1与G-N2收尾 | 两方发现项→作者修正并明确版本范围 | Grok有效检视3f4a86ec1e854356b1c5776a37054e9e；[处理记录](../drafts/BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | Codex按负责人决定处理 | 仅文档；末次修订未另行独立复查，行为效果未验证 |
| 2026-11-16 12:56:49 +08:00 | entry-3353／文档清理与正式化 | 旧表述→删除指定提及并关联正式记录 | 用户要求；[前置记录](../drafts/BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | 用户授权；Codex实施 | 仅文档；作者静态检查，两方检视待完成 |
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-15 19:02:52 +08:00 | 文档收口 | 待复核→复核发现项已处理 | Grok独立复核，Codex修正文字及索引分组；方案不变 | Grok复核；Codex处理 | 本主题状态摘要 |
| 2026-11-15 18:53:06 +08:00 | 1.0／分区 | 无流程教训分区→建立正式记录入口 | entry-3085用户条件授权；两端明确支持，区别于事故复盘 | Codex整理；Grok、DeepSeek目录评估 | 本目录及必要索引，不迁移旧材料 |
