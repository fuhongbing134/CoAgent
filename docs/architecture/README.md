# docs/architecture/ — 架构文档

本目录存放架构现状记录与改进方向，遵循 [design-decision-record-spec](../../specs/DESIGN_DECISION_RECORD_SPEC.md) 与 [document-management-spec](../../specs/DOCUMENT_MANAGEMENT_SPEC.md)。摘要按正文已有证据记录。总导航见[文档索引](../README.md)。

| 主题 | 文档 | 现状 |
|---|---|---|
| 架构现状与改进方向 | [ARCHITECTURE.md](ARCHITECTURE.md) | 官方现状图已定稿；改进方向为提案未实施；adapter execute 已收敛，普通文件退路延后，共享适配唯一性保留分歧待裁决 |
| 工作台单 Agent 调用与多 Agent 协作流程 | [WORKBENCH_CALL_FLOW.md](WORKBENCH_CALL_FLOW.md) | 三端终审通过，已形成详细图文统一版；共识限于源码静态核对，Mermaid 渲染与真实 CLI 未验证；历史及逐项处理从正文进入 |
