# 旧并发压测废案

状态：废弃、仅供历史追溯。用户来源entry-3310，归档时间2026-11-16 11:25:17 +08:00（Python本机时钟）。用户要求旧成果不作新基线并移入废案，防止误用。归档不是对旧方案、工具或结果的批准，不授权恢复执行。

依据及教训候选：[../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)。此句原为归档时点状态；后续三方讨论及entry-3327规范修订见主题稿§7—§12，本轮正式记录见[../../../lessons/BENCHMARK_RESTART_LESSONS.md](../../../lessons/BENCHMARK_RESTART_LESSONS.md)，两方检视待完成。

## 文档及工具入口

- [CONCURRENCY_BENCHMARK_DESIGN.md](CONCURRENCY_BENCHMARK_DESIGN.md)：旧设计，原计划与未完成状态作为历史保留。
- [CONCURRENCY_BENCHMARK_RESULTS.md](CONCURRENCY_BENCHMARK_RESULTS.md)：历史实施、失败及部分验证，不能外推容量或完整验收。
- [CONCURRENCY_PREFLIGHT_DISCUSSION.md](CONCURRENCY_PREFLIGHT_DISCUSSION.md)：entry-3284前置留稿及预检结果。
- [tools/audit_concurrency.py](tools/audit_concurrency.py)
- [tools/benchmark_concurrency.py](tools/benchmark_concurrency.py)
- [tools/test_benchmark_concurrency.py](tools/test_benchmark_concurrency.py)

三个工具保持原始字节，仅移动路径；REPO定位、根模块导入及旧命令仍反映原目录结构，不作为当前可运行工具。未修复、未运行。根目录业务模块、通用测试与P0-2后端能力探测保持原位。

## 迁移清单

| 交付对象的历史路径 | 交付对象当前链接 |
|---|---|
| `docs/design/CONCURRENCY_BENCHMARK_DESIGN.md` | [CONCURRENCY_BENCHMARK_DESIGN.md](CONCURRENCY_BENCHMARK_DESIGN.md) |
| `docs/design/CONCURRENCY_BENCHMARK_RESULTS.md` | [CONCURRENCY_BENCHMARK_RESULTS.md](CONCURRENCY_BENCHMARK_RESULTS.md) |
| `docs/drafts/CONCURRENCY_PREFLIGHT_DISCUSSION.md` | [CONCURRENCY_PREFLIGHT_DISCUSSION.md](CONCURRENCY_PREFLIGHT_DISCUSSION.md) |
| `audit_concurrency.py` | [tools/audit_concurrency.py](tools/audit_concurrency.py) |
| `benchmark_concurrency.py` | [tools/benchmark_concurrency.py](tools/benchmark_concurrency.py) |
| `test_benchmark_concurrency.py` | [tools/test_benchmark_concurrency.py](tools/test_benchmark_concurrency.py) |


## 完整性与限制

DeepSeek在主题稿0.3阶段核验归档文档，来源调用6e06d6a155804592bc53762c4779dad7；此处仅引用当时报告，不覆盖后续修改。旧测试覆盖和工具可运行性未因此得到验证。


归档文档已增加当前说明、链接和历史，旧工具保持废案状态。当时10份文档相对链接检查无缺失；未重新验证旧工具或旧实验结论，不将归档成功补作历史实验通过证据。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 12:56:49 +08:00 | entry-3353／文档清理与正式化 | 旧表述→删除指定提及并关联正式记录 | 用户要求；[前置记录](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | 用户授权；Codex实施 | 仅文档；作者静态检查，两方检视待完成 |
| 2026-11-16 12:24:58 +08:00 | entry-3327／验收返修 | 首轮清理→补语义及交付链接检查 | 主题草稿§12；Python本机时钟 | Codex | 作者静态检查；返修版本待独立复核 |
| 2026-11-16 12:15:57 +08:00 | entry-3327／交付引用清理 | 具体本地定位→可交付摘要或通用占位符 | 用户要求；主题草稿§12；Python本机时钟 | Codex | 保留历史事实与版本范围；作者检查，待独立复核 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／归档入口 | 无→废案归档说明 | 用户要求隔离旧压测；主题前置留稿；Python本机时钟 | 用户；Codex实施 | 保留历史与证据，不作为新测试基线 |
