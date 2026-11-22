# 硬编码与绝对路径全量排查报告

| 字段 | 值 |
|---|---|
| 版本 | 0.1 |
| 状态 | 讨论中（改进仅为建议，尚未授权或实施） |
| 来源 | entry-4278 三端只读清点与汇总（Codex / DeepSeek Harness / Grok） |
| 基准 | 提交 `88b3b73751ccfaeaf66402e9f310ee5e64e94ae0`（`feature/GitDiscussion`） |

## 概述

本报告记录对 `step1` 项目进行的一次全量只读排查，目标为定位项目中「硬编码值、绝对路径及类似固定项」，逐条标明位置、类别、推测原因、改进方式，以及改进与不改进的后果，并按优先级排序。排查仅做静态清点与归因，**未实施任何修改**；文中的「改进方式」均为建议，须另行授权并评估后再落地。

## 排查范围与方法

- **workbench/**（45 个 Python 文件，含 `web/page.py` 内嵌 JavaScript）—— Codex 清点，编号 W01—W33。
- **tests/**（unit/verify/fixtures）＋ 根目录脚本与配置（`main.py`、`web_chat.py`、`agent_bridge.py`、`inspect_execution.py`、`collaboration_ui.js`、`requirements.txt`、`.codex/config.toml`、`dsh-headless-auth.patch.yml` 等）＋ `tools/` —— DeepSeek Harness 清点，编号 R1—R6、T1—T6。
- **docs/** 与 **specs/** —— Grok 清点，编号 D01—D08、H01—H08。
- 排除自动生成物：`.venv`、`.git`、`test-artifacts` 等。

类别覆盖：绝对路径、硬编码可执行文件/CLI 路径或名称、魔法数字（端口/超时/阈值）、硬编码 URL/端点、凭据、环境变量名/文件名/目录名。位置一律用仓库相对路径表示；涉及个人主机绝对路径的字面值按文档管理规范用占位符表示，不在本报告中作为定位引用。

## 总体结论

真正的「硬编码/绝对路径」问题集中在三处：**① 本机目录 + 分支写进代码的权限授权（W01/W02）**；**② CLI 发现与厂商安装布局在 workbench、根 `web_chat.py`、测试三处重复维护**；**③ `requirements.txt` 依赖清单不全（R5）**。其余大多是「魔法数字/阈值分散」与「应保留的协议/契约常量」，以及文档层的历史引用。三份排查均未发现内嵌真实凭据。

## A．高优先级：本机路径 / 分支 / 权限绑定（可移植性 + 安全边界）

| 项 | 位置 / 字面值 | 类别 | 原因 | 改进方式 | 改进后后果 | 不改进后果 |
|---|---|---|---|---|---|---|
| W01 | `workbench/adapters/web_backends.py:111` 硬编码本机 step1 项目绝对路径（个人用户名段省略）；`:113–114` `step1-git` | 绝对路径、权限配置绑定 | 仅对本机指定目录启用特定 Codex 权限配置 | 改为显式受控「工作区→权限配置」映射，默认收紧、可审计 | 可迁移、可审计；但配置错误可能扩大权限，需谨慎 | 换用户/移动目录后静默失去此配置 |
| W02 | `workbench/adapters/web_backends.py:306,315` 模块上两级目录 + `feature/GitDiscussion` | 目录结构、分支授权绑定 | Grok Git 工具仅对本项目及指定分支开放 | 试验授权独立为受控策略，显式显示生效条件 | 便于维护、防权限扩散 | 换分支即失效，试验规则混入生产路径 |
| R5 | `requirements.txt` 仅 `mcp>=1.9,<2` | 依赖清单缺口 | 依赖清单未同步实际安装 | 补全 uvicorn/python-dotenv/pywin32 等或 `pip freeze` 锁版本 | 环境可复现、重建 venv 不丢依赖 | 重建 venv 丢依赖，与 DLL 问题叠加 |

## B．CLI 发现与厂商安装布局耦合 + 多处重复维护（高/中）

| 项 | 位置 / 字面值 | 类别 | 原因 | 改进方式 | 改进后后果 | 不改进后果 |
|---|---|---|---|---|---|---|
| W03–W07 | `workbench/adapters/web_backends.py:23,36,43,46,51,59,63–69,232` 与 `workbench/adapters/deepseek_command.py:12,16–31,43`：`DSH_CLI/GROK_CLI/CODEX_CLI`、`USERPROFILE/.grok/bin/grok.exe`、`LOCALAPPDATA/OpenAI/Codex/bin/*/codex.exe`（按修改时间取最新）、`DeepSeek Harness.exe`+`app.asar/…/bin.js`、`COMSPEC`/`ELECTRON_RUN_AS_NODE` | 环境变量、CLI 名、厂商安装布局 | 三个 CLI 各有厂商默认布局，代码做多候选自动发现 | 收敛为单一 resolver + 显式 `*_CLI` 覆盖 + 版本/可运行性诊断；布局兼容逻辑集中并按版本核验 | 减少升级耦合、多版本可诊断；需验证启动行为 | 厂商改目录/打包布局后静默失败，或选到非预期版本 |
| R1 | `main.py:39–54` 硬编码 `codex` + 参数；`:61` `timeout=300` | CLI 名、魔法数字 | 独立 REPL 假设 `codex` 在 PATH | 复用 resolver + `CODEX_CLI` 覆盖，timeout 命名/可配 | 可复用、可诊断 | PATH 无 codex 或参数变更即失效 |
| R2 | `web_chat.py`（根，约 23KB）整段重复 CLI 解析/配置（`CODEX_SANDBOX="workspace-write"`、`DSH_PROFILE="headless"`、`DSH_AUTH_PATCH`、超时 `1200`、`127.0.0.1`） | CLI 解析、配置、重复维护 | 根目录早期单体 web 服务，与 workbench 重复 | 收敛为单一实现/共享常量 | 消除两处漂移 | 根与 workbench 各自演进、易不一致 |
| T4 | `tests/unit/test_concurrency_backend.py:93–165,224` 内联重复 resolver | CLI 解析、重复维护 | 测试为独立控制 backend 复制了 resolver | 复用 workbench.adapters resolver，测试只覆盖差异 | 测试不再漂移 | 上游 resolver 变更后测试测旧逻辑 |
| D03/D04 | `docs/guides/MCP_SETUP.md:8,11,59–79,109–115` GROK/DSH 安装布局 | 文档、厂商布局 | 记录官方发现顺序 | 标「厂商默认，用 `*_CLI` 覆盖」 | 排障更快 | 厂商改目录后文档指向旧位置 |

## C．魔法数字 / 阈值分散（中）

- **探测主机/远端限制**：`workbench/agents/capability_probe.py:18` `github.com`/`chatgpt.com`、`:125,129–135` 仅 GitHub HTTPS 正则 + 禁重写/跳转（W09/W10）——标注「只代表这两个目标」，按需提供受控托管方策略。
- **执行/生命周期超时**：`workbench/execution/managed_process.py:232–233` 300s/5s/5s + 轮询 0.01s；`workbench/execution/web_runs.py:136,143,165` taskkill/8s/2s；`workbench/web/page.py:193,760` 10s/3s；`workbench/adapters/web_backends.py:311` 5s（W08/W13/W16/W17）——集中命名生命周期策略。
- **SQLite 锁等待**：多处 10s，另有 2s/5s/0s（W14/W15）——按操作类型集中命名，不要机械统一。
- **消息/输入/诊断边界**：20000 字符、50/100/200 条、100000 字节、4096 字节、ID 200 字符、截断 500/1000 字符、诊断 1 MiB、最近任务 LIMIT 20、操作预算 2–100（W18–W23）——按接口命名并解释单位，保持前后端一致。
- **测试/冒烟端口与等待**：`tests/verify/smoke_test.py:56` 端口 `8765`；`tests/unit/test_collaboration_actions.py:89` 5s、`test_capabilities.py:121` 3s、`test_managed_process.py:279` 30s（T3/T6）——端口与工作台默认端口统一来源。
- **Codex sandbox 名**：`.codex/config.toml` + `tests/verify/run_mxc_unit.py:78` `mxc`（R3）——集中命名并文档化。

通用结论：命名常量、集中、解释边界即可，**不必一律新增环境变量**。

## D．低优先级 / 保留契约（通常不改）

- **安全/同源边界**：`127.0.0.1`/`localhost`/`http://`/`X-Capability-Token` 回环与令牌授权（W24）——保留。
- **入口/内部名/锁文件/版本**：`AGENTS.md`+`specs/README.md`、`agent-workbench-` 等内部文件名、`.migration.lock`/`.service.lock`、MCP `2024-11-05`、DB 版本 2、Win32 常量与裸 `8192`（W26/W27/W28/W31/W32）——保留，裸 8192 补语义名。
- **适配器白名单 / grok 完成契约**：`codex/dsh/grok` 白名单、仅接受 `end_turn/EndTurn`（W29/W30）——扩展后端时同步校验；不能把未知 stopReason 全当成功。
- **其余低影响**：`zh-CN`/`AgentWorkbench/0.1`（W33）、`dsh-headless-auth.patch.yml` 三个 id（R4）、`collaboration_ui.js:34` 相对端点（R6）、`tests/verify/verify_real_backends.py:3` docstring 本机 Python 绝对路径（T1）、`tests/verify/run_mxc_unit.py:59–61` 可覆盖默认值（T2）、测试回环 127.0.0.1（T5）。

## E．文档层（低，随代码跟进）

- D01：指南 `http://127.0.0.1:8765` 默认端口 → 注明「默认，可用 `--port` 覆盖」。
- D02：指南 Windows `.\.venv\Scripts\python.exe` → 补 POSIX 等价写法。
- D05：指南探测主机 github.com/chatgpt.com → 跟随代码。
- D07：指南超时/刷新数字 → 注明「与代码默认一致，以环境变量/启动参数为准」。
- D08：示例身份 `vendor="OpenAI"`/`model="deepseek-v4-pro"` → 改成「填当前模型名」。
- H01–H08：历史/取证/归档引用（PID、SHA、外部 URL、未落地草稿）→ 不改。

## 凭据结论

三份排查均未发现内嵌真实密钥/密码；`token`/`secret` 命中均为脱敏规则或测试样例；`CAPABILITY_TOKEN` 为外部注入值。**未做专门秘密扫描或 Git 历史审计**，此结论仅限静态字面检查，不视为已完成凭据审计。

## 缺口与限制

- 用途/动机按当前实现推断，未逐项追溯最初需求；本报告不能替代原作者决策记录。
- 未逐条枚举 SQL 字段、HTTP 状态码、全部 sleep/超时数值；未核对文档数字与代码默认是否逐字一致。
- `postmortem/` 与根 `README.md`（在 `docs/` 之外）未纳入本轮扫描。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-21 22:27:23 +08:00 | 0.1／硬编码与绝对路径全量排查报告 | 无→新增讨论草稿 | 用户要求把 entry-4278 三端只读清点与汇总讨论落为独立文档；内容沿用已结案汇总（W01—W33、R1—R6/T1—T6、D01—D08/H01—H08）。本机时钟 | 用户提出；DeepSeek Harness 整理 | 新增草稿文档及 docs/drafts/README.md 索引条目 |
