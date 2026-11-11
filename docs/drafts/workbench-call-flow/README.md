# 工作台调用流程：原稿、互审与修订依据

entry-2903，2026-11-11。用户要求三端独立绘制单Agent调用、多Agent协作流程，互审讨论后汇总。统一稿经 DeepSeek Harness、Grok 终审同意及 Codex 收口，三端终审通过，无阻断异议；共识限于源码静态核对，Mermaid 渲染、真实 CLI 与故障分支仍未验证。entry-2916 据此同步当前状态，原稿和互审保留当时语境。

当前阅读入口：[工作台调用流程统一稿](../../architecture/WORKBENCH_CALL_FLOW.md)。本目录保存作者当时观点及修订理由；原稿中的错误箭头和实例按历史保留，当前解释以统一稿及[处理表](RESOLUTION.md)为准。

| 材料 | 作者与用途 |
|---|---|
| [CODEX.md](CODEX.md) | Codex独立原稿 |
| [DEEPSEEK_HARNESS.md](DEEPSEEK_HARNESS.md) | DeepSeek Harness独立原稿；实际磁盘288行，早期交接247行不是当前清单依据 |
| [GROK.md](GROK.md) | Grok独立原稿 |
| [REVIEW_CODEX.md](REVIEW_CODEX.md) | F01–F12，含自稿修正 |
| [REVIEW_DSH.md](REVIEW_DSH.md) | D01–D11，含未核对范围 |
| [REVIEW_GROK.md](REVIEW_GROK.md) | G01–G11及GS01–GS10 |
| [RESOLUTION.md](RESOLUTION.md) | 44项意见映射、源码裁定、后继修正与保留意见 |

本目录属于讨论与历史依据，不是新功能计划。未改写原稿、没有执行Git提交；源码版本与未验证边界见统一稿。相关[文档总索引](../../README.md)、[草稿索引](../README.md)、[规范索引](../../../specs/README.md)。
