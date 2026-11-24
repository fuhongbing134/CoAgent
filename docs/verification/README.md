# 验证报告

| 文档 | 说明 |
|---|---|
| [CONCURRENCY_LOAD_TEST_REPORT.md](CONCURRENCY_LOAD_TEST_REPORT.md) | 0.8 正式归档，记录受控试跑、r7 隔离补测及未验证边界 |
| [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) | 1.0 正式验收记录。六项原定主要验收通过，复验边界见报告第 11 节 |
| [WEB_COLLABORATION_ACCEPTANCE.md](WEB_COLLABORATION_ACCEPTANCE.md) | 2026-10-20 历史联调记录及尚未完成的验收 |

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-24 19:28:26 +08:00 | 六项／验证报告 1.0 | 0.10 分项摘要→正式验收入口 | [验收报告第 11 节](DLL_MXC_IMPROVEMENTS_VERIFICATION.md#11-收尾结论与复验边界)。本机时钟 | 用户确认收尾，Codex 整理 | 验证索引 |
| 2026-11-24 19:23:00 +08:00 | 复盘后续六项／事项 1 新版最终汇总 | 0.9 入口摘要→记录 0.10 新版只读汇总通过 | [验证报告第 9.6 节](DLL_MXC_IMPROVEMENTS_VERIFICATION.md#96-新版只读最终汇总实测)的主库核对。本机时钟 | 用户实跑，Codex 核对和整理 | 验证索引 |
| 2026-11-24 19:12:22 +08:00 | 复盘后续六项／事项 2 WSL POSIX 回归 | 0.8 入口摘要→记录 0.9 POSIX 回归通过 | 13 项实跑及文件锁证据见 [验证报告第 10.2 节](DLL_MXC_IMPROVEMENTS_VERIFICATION.md#102-事项-2-的-wsl-posix-回归)。本机时钟 | 用户要求继续，Codex 实测和整理 | 验证索引 |
| 2026-11-24 18:30:02 +08:00 | 复盘后续六项／事项 6 HTTP 故障复验 | 0.7 入口摘要→记录 0.8 同次链路 | 双组对照及返回字段见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 10.1 节。本机时钟 | 用户提出，Codex 实测并归档 | 验证索引 |
| 2026-11-24 00:35:43 +08:00 | 复盘后续六项／事项 1 通过项补录 | 0.6 入口摘要→记录 0.7 续接、目录和 CLI 对照 | 用户实测与主库复核见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 9.5 节。本机时钟 | 用户要求详细记录，Codex 核对整理 | 验证索引 |
| 2026-11-24 00:14:14+08:00 | 事项 2、5、6 补验 | 待补验→记录结果 | Windows 实跑及受控注入，见报告第 10 节。本机时钟 | 用户授权，Codex 验证与归档 | 补验记录 |
| 2026-11-24 00:10:04 +08:00 | 复盘后续六项／主工作台复验 | 0.4 入口摘要→记录 0.5 主工作台正例 | 用户实测，核对同次快照，见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 9.4 节。本机时钟 | 用户实测，Codex 核对并整理 | 验证索引 |
| 2026-11-23 23:59:31 +08:00 | 复盘后续六项／事项 1 后端采集 | 0.3 入口摘要→记录 0.4 工作台采集正例 | 真实调用和追加回归见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 9 节。本机时钟 | 用户授权，Codex 整理 | 验证索引 |
| 2026-11-23 23:34:07 +08:00 | 复盘后续六项／事项 4 验证 | 0.2 入口摘要→记录 0.3 Grok 验收 | 真实工作台与独立 MCP 实测见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 8 节。本机时钟 | 用户授权，Codex 整理 | 验证索引 |
| 2026-11-23 23:23:52 +08:00 | 复盘后续六项／事项 3 验证 | 0.1 入口摘要→记录 0.2 单测正例 | 真实 MXC 运行与入口分类回归，见 [DLL_MXC_IMPROVEMENTS_VERIFICATION.md](DLL_MXC_IMPROVEMENTS_VERIFICATION.md) 第 7 节。本机时钟 | 用户授权，Codex 整理 | 验证索引 |
| 2026-11-23 22:57:27 +08:00 | 复盘后续六项／验证入口 | 无→新增分项验证报告入口 | 用户指定归档至验证分区，形成依据见 [设计草稿第 14 节](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#14-后续验证记录形成)。本机时钟 | 用户提出，Codex 整理 | 验证索引 |
| 2026-11-20 11:21:44 +08:00 | entry-3821／验证索引同步 | 原导航→归位后导航 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 路径及状态摘要 |
| 2026-11-19 01:56:13 +08:00 | entry-3668／验证报告入口 | 无→正式报告入口 | [同版待审基线](../drafts/CONCURRENCY_LOAD_TEST_REPORT_R7.md)，本机时钟 | 用户委托，Codex 归档 | 初次建立分区索引 |
