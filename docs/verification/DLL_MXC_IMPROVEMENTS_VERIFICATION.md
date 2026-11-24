# DLL/MXC 复盘后续 6 项改进验证记录

| 项目 | 本次记录 |
|---|---|
| 报告状态 | 1.0 正式验收记录。六项原定主要验收通过，2026-11-24 完成收尾。[复验边界](#11-收尾结论与复验边界) |
| 验证日期 | 2026-11-23 至 2026-11-24，时间均为北京时间 UTC+08:00。时间表区分工作台结果时间和终端诊断时间 |
| 需求与设计 | [REQ-003 1.0](../requirements/DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md) · [方案设计 1.0](../design/DLL_MXC_IMPROVEMENTS_DESIGN.md) · [本报告的形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#14-后续验证记录形成) |
| 环境 | Windows，成功阶段用系统安装的基础 Python。事项 2 另在 WSL 2 的 Ubuntu 中使用 Linux Python 完成 POSIX 回归。事项 1 早期隔离实例使用 `8766`，主工作台复验使用 `8765`。事项 1 后续换目录与 CLI 对照、事项 4 使用系统分配的独立回环端口和独立数据库 |
| 被测版本 | `feature/dll-mxc-improvements` 工作树，基线提交 `35a0380ef741e9ab4e0e05630fba844de049b046`，含未提交的诊断改动。新版实测的 Codex CLI 版本为 `0.167.0-alpha.3.2`，各次可执行文件身份见对应快照。适用的源码指纹见各节 |
| 对照范围 | 复盘中原有 DLL 验收只作为历史背景。下面的通过结论限于本次运行的现场版本和实际场景。[原始调查](../drafts/CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md) · [正式教训](../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md) |

## 1. 六项范围与本次结论

| 事项 | 本轮证据 | 状态与剩余边界 |
|---|---|---|
| 1. Codex CLI 身份与观测后端 | 新建、续接、只读最终汇总均有新版运行快照。新建、续接、CLI 覆盖 A/B 具备同次 MXC 进程链。其他目录按请求启动并采用默认 `workspace-write` | **原定验收通过**。最终汇总与其他目录样本的实际后端均为 `unknown`。其他目录的独立后端正例仍是补充证据。见第 9.5、9.6 节 |
| 2. 迁移屏障错误分类 | Windows 分类与互斥通过。Ubuntu WSL 2 的 13 项迁移回归与实际文件锁错误探针均通过 | **通过**。确认正常获锁、跨进程共享兼容与独占互斥、分类错误、异常链及释放后重获锁。见第 10、10.2 节 |
| 3. MXC 单测入口与阶段定位 | 真实 Windows MXC 链路取得同次进程链与回环证据。后续全量回归执行 233 项，232 项通过、1 项按配置跳过 | **通过**。无效 CLI 和宿主夹具失败经实际入口验证。其余阶段定位由 17 项受控专项回归覆盖。见第 7、9.3 节 |
| 4. Grok 文件工具子进程身份与原生依赖 | 隔离工作台实测 Grok 调用 MCP 创建文件，独立核对内容和该次子进程解释器。真实工作台复验 `ImportError`，快照记录 `native_load` 且原文件保持不变。独立 stdio 探针验证 `OSError` 通道与越界拒绝 | **通过**。故障复验观测到两份不同的 PID，产品摘要将单一身份标记为 `unknown`。各通道和证据粒度见第 8 节 |
| 5. 双进程 Job 归属、取消与退出 | 基础 Python 同 PID，虚拟环境双 PID。两组实测均核对 Job 归属、取消、进程退出及 Job 清空 | **通过**。两组均为 `cancelled / confirmed`，真实系统直接拒绝未单独取证。见第 10 节 |
| 6. 宿主探测与 Agent 能力边界 | 正常对照与受控 `ssl` 故障均从真实 HTTP 入口触发。故障只影响 HTTPS，文件和本地 Git 照常返回，Agent 标记 `not_tested` | **HTTP JSON 验收通过**。正常组使用受控 HTTPS 响应，聊天页面无探测结果展示。见第 10.1 节 |

## 2. 从启动受阻到能发起真实调用

### 2.1 区分三个启动阶段

| 步骤 | 实际证据 | 处理及边界 |
|---|---|---|
| 在受限会话内启动额外工作台 | 早期隔离工作台接收了新建请求。使用项目临时目录后，子 CLI 启动并以失败退出。stderr 先报告 `.codex\tmp\arg0-windows` 创建 PATH 别名失败，随后报告 `failed to initialize in-process app-server client` 和 `os error 5`。后端快照为 `unknown` | 这一轮只能确认内层 CLI 在初始化受阻。外层运行上下文能写项目目录，用户级 `.codex` 不在相同写入范围。app-server 自己的确切失败操作未定位。续接和只读汇总当时尚未实跑 |
| 从普通 PowerShell 启动 `8766`，将测试库放在用户临时目录 | `ServiceInstanceLock` 在 `os.open(..., O_RDWR \| O_CREAT)` 创建数据库旁的 `.service.lock` 文件时收到 `PermissionError: [Errno 13] Permission denied`。调用发生在 `server_bind()` 之前 | 把隔离数据库和 `TEMP`、`TMP`、`TMPDIR` 指到项目内独立目录。此后 `8766` 能启动，且与主工作台分用端口和数据库。[数据库选择与服务启动](../../web_chat.py) · [实例锁实现](../../workbench/execution/service_instance.py) |
| 改用基础 Python 重启时从诊断窗口发出启动命令 | 命令没有带入验收窗口的 `AGENT_BRIDGE_DB`，工作台回退到主库。锁文件已打开，`msvcrt.locking(..., LK_NBLCK, 1)` 返回 `Permission denied`，最终异常为 `Cannot acquire database execution-service lock`，对象是主库的实例锁 | 在普通 PowerShell 显式重设相同的验收库、指定 CLI 和临时目录，再用基础 Python 启动 `8766`。如果旧验收实例仍在线，从它自己的启动窗口停止后再复用端口和测试库。主工作台无需停止 |

### 2.2 确认页面能发给 Codex

隔离数据库初始的 Agent 登记列表为空。`GET /api/agents` 实测返回 `codex_cli_available=true`、`dsh_cli_available=true`、`grok_cli_available=true`。这三个值确认 CLI 路径可解析。DeepSeek Harness 在页面还需要本库中的登记记录。默认广播仍把 DeepSeek 列作收件人，所以前端在有不可用收件人时禁用发送。校对时库中没有消息或运行，符合提交前被拦下的表现。[收件人与提交逻辑](../../workbench/web/page.py) · [消息路由](../../workbench/web/routes.py)

普通新建和续接改走 `POST /api/messages`，显式指定 `recipients=["codex"]`，正文不带 Agent 点名。这样可在同一全新对话中检验两条普通执行路径。最终汇总使用页面点名进入协作，通过任务卡触发。

### 2.3 捕获通用错误下的实际启动证据

`22:33:27` 和 `22:34:34`，验收工作台各有一条新建失败。两次请求均已写入，结束状态为 `failed`，`exit_confirmed=1`，无取消标志，无会话 ID，后端记录 `unknown`。页面只显示 `Managed process failed: failed`。当前 [进程错误处理](../../workbench/execution/web_runs.py) 对 Codex 的通用报错没有附出原始 stderr，因而这两条记录本身无法给出确切退出码或 Windows 操作。

为缩小范围，从普通 PowerShell 使用同一选定 CLI、同一项目工作目录及相同的 `step1-git` 配置，按下表比较三种启动方式。Python 两次对照都用 `subprocess.run`、同一参数表、UTF-8 输入输出、从标准输入传入仅回复 `OK` 的提示。

| 启动路径 | 实际结果 | 证据含义 |
|---|---|---|
| 项目 `.venv\Scripts\python.exe` 启动 Python 探针 | `EXIT=1`，stdout 为空。stderr 先出现清理旧 arg0 目录及创建 PATH 别名被拒，随后 app-server 初始化报 `os error 5` | 通过原虚拟环境的 Python 启动同一 CLI，可在工作台 HTTP 之外复现启动异常 |
| PowerShell 直接启动同一 `codex.exe` | `EXIT=0`。输出 `thread.started`、`turn.started`、回答 `OK`、`turn.completed`。另有模型列表刷新超时，以及系统技能目录删除遇到 `os error 145` | CLI 可直接完成一次新会话。此轮使用命令行提示，未隔离标准输入及父进程的全部差异 |
| 基础 `python.exe` 启动同一 Python 探针 | `EXIT=0`，同样完成新会话并回复 `OK`。stderr 有模型列表刷新超时，另有 Responses WebSocket 连接失败 | 与 `.venv` 探针相比，换掉外层解释器启动路径后能完成。同一运行中网络告警未阻断最终回答 |

上述证据支持把基础 Python 作为这次隔离工作台的宿主启动方式。不能由此确定具体的 Windows 拒绝对象、进程令牌差异、父 Job 或安全软件行为，也不能把早期所有 `Managed process failed` 都认定为同一原因。探针独立创建的会话仅用于启动链对照，真实工作台验收以第 3 节为准。

## 3. 事项 1 的真实工作台验收证据

成功实例继续使用 `8766` 和隔离数据库，普通调用的工作目录仍是项目根。下表工序栏的时间来自工作台消息 `created_at`，从 UTC 换算到北京时间。消息时间和 CLI 实际执行时间分别记录。

| 工序 | 操作与实际回复 | 工作台和会话证据 |
|---|---|---|
| 新建 · 22:46:01 | 同一默认测试对话首条实际成功消息仅发 Codex。要求执行一次 `Get-Location`、记住验收码 `A731`。`22:45:38.971` 记录命令完成并返回项目目录。CLI 轮次于 `22:45:42.726` 完成 | `launch_mode=new`，`permission_mode=default`，`permission_profile=step1-git`，运行 `completed`、`exit_confirmed=1`，成功落下会话绑定 |
| 续接 · 22:48:36 | 在相同对话再次仅发 Codex，要求执行 `Get-Location` 后回忆上一轮验收码。`22:48:11.575` 记录命令完成，CLI 轮次于 `22:48:20.695` 完成。第二条消息没有再次给出验收码，实际回复 `A731` | `launch_mode=resume`，`permission_mode=default`，`permission_profile=step1-git`，运行 `completed`、`exit_confirmed=1`。对照保存的会话绑定及两轮会话上下文，确认同一 Codex 会话 |
| 协作入口 · 22:50:37 | 点名 Codex 建立验收任务，指定其为负责人和最终答复者，要求先询问验收编号。Codex 实际询问后等待用户输入 | 入口为 `role=entry`，协议状态 `waiting_user`。这条入口调用为普通权限，单独列出便于与最终汇总区别 |
| 只读最终汇总 · 22:50:51 | 在任务卡点 **按当前成果给结论**。系统触发单独汇总，`22:51:02.774` 的 CLI 轮次上下文为只读，`22:51:18.392` 轮次完成。最终回复说明未收到验收编号，等待任务提前收口 | `role=final`，`agent_id=codex`，`launch_mode=resume`，`sandbox=read-only`，`permission_mode=read-only`，`permission_profile=null`。运行、协作调用及最后交付均为 `completed`，`exit_confirmed=1`、`cancel_requested=0`。该轮没有终端操作 |

最终正文所说的「验收已取消」描述的是等待编号的活动。数据库记录显示真实最终汇总调用启动并完成，工作台进入已交付状态。本轮通过项是**只读汇总路由、请求的只读启动参数和成功交付**。本轮没有用实际写操作测试 Windows 写入限制。[任务卡操作](../../workbench/web/page.py) · [最终角色调度](../../workbench/workers/dispatch.py) · [Codex 只读参数选择](../../workbench/adapters/web_backends.py)

三条通过运行的 CLI 身份均由快照记录，`cli_version=0.167.0-alpha.3.2`，`executable` 指向同一显式选定的 `codex.exe`。普通权限模式 `default` 与具体命名配置 `step1-git` 是独立字段，最终只读没有另指定命名配置。实际工作目录始终为项目根，尚无另一工作目录走默认 `workspace-write` 的对应样本。

## 4. MXC 后端证据的判定

- 三次通过运行的 `observed_backend` 和 `backend_evidence_source` 均是 `unknown`。这是运行快照的实测值，和命名权限配置、只读模式及调用是否完成各自独立。
- 这三次验收时的实现只从同次调用的 stderr 抽取唯一的 `Windows sandbox selection` 文本。这个版本的上游 Codex 由 Windows 沙箱日志写入函数记录该文本。[该版本的 Codex 选型源码](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/core/src/windows_sandbox.rs#L57-L76) · [按日写入实现](https://github.com/openai/codex/blob/rust-v0.167.0-alpha.3.2/codex-rs/windows-sandbox-rs/src/logging.rs#L130-L134)
- 同期看到过 `Windows sandbox selection: mxc`。共享日志没有足够信息把这些行逐一绑定到三条验收运行，包含其他 Codex 活动的时段尤其不能只按秒数匹配。项目请求的后端和可用性检测只能提供上下文，不能回填已观测后端。
- 这些快照保留当时的 `unknown`。后续补正后的工作台正例及其范围见第 9 节。

## 5. 事项 3 的既往前置阻塞

在真实工作台的三条路径跑通之前，对当前改进的 [MXC 单测入口](../../tests/verify/run_mxc_unit.py) 做过独立验证尝试。外层明确使用基础 Python，内层请求项目 `.venv`，CLI 为 `0.167.0-alpha.3.2`，请求 `windows.sandbox=mxc` 和 `step1-git`。两步分别观察到：

1. 运行器在 `prepare_links()` 的首次 `os.link()` 创建硬链接夹具时收到 `[WinError 5]`，退出 1。代码尚未调用 MXC 启动内层 Python，也尚未输出五阶段的完整分类。
2. 绕过夹具的独立最小原生 MXC 探针同样退出 1，明确报 `failed to prepare MXC sandbox: native MXC is unavailable on this executor`。该判定属于当时的执行器。内层运行标记和同一上下文回环结果均未产生。

按设计，成功记录需要能依次辨别 CLI、MXC、内层解释器、同一上下文回环和真实套件阶段。此次只取得前置阻塞的正面证据。DLL 原故障处置曾在不同版本和上下文通过完整单测，历史报告中的成功数字不能用来补齐这次改进的验收。[方案设计第 6 节](../design/DLL_MXC_IMPROVEMENTS_DESIGN.md#6-详细设计) · [原始调查的单测验证](../drafts/CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md)

## 6. 复跑与验收记录方式

1. 从普通 PowerShell 打开项目根。为本次验证新建项目内的独立测试目录和可写子目录，将 `AGENT_BRIDGE_DB` 指到其中的新 SQLite 文件，把 `TEMP`、`TMP`、`TMPDIR` 指到可写子目录。指定要验收的 `CODEX_CLI` 可执行文件。用基础 Python 直接运行工作台入口并指定独立端口。确认打印的数据库属于验收实例。[工作台入口](../../web_chat.py)
2. 对 `POST /api/messages` 发送新的对话 ID、仅包含 `codex` 的 `recipients` 和不带点名的内容。第一轮给定一个临时验收码。等回复完成并保存会话绑定后，用同一对话 ID 发第二轮，要求回忆验收码。`accepted` 仅表示请求已进入工作台。
3. 在验收实例发起以 Codex 为最终答复者的协作任务。等待任务卡显示最终答复者及等待状态，再点 **按当前成果给结论**。分别读取协作入口和最终调用，检查真正的 `role=final`、只读快照和最终交付状态。
4. 每次核对 `execution_runs` 的运行状态、退出确认及配置快照，使用 `source_job_id` 与 `source_call_id` 关联 `collab_calls`，核实最终调用角色及其状态。新建与续接还要查询该对话实际保存的会话绑定。当前现场绑定在 `workbench_sessions.codex_thread_id`。
5. 需验收实际后端时，逐条保留可对应到当前运行的 Windows 后端证据，核对快照中的值及来源。证据缺失或不能归属时记录 `unknown`，同时保留功能运行是否通过的独立结果。

0.1 归档时只读核对既有运行和源码。0.2 新增事项 3 补正后的运行与回归结果。

## 7. 事项 3 补正、改动原因与复验

### 7.1 补正前的证据

普通 Windows PowerShell 运行原生 MXC 最小探针，得到 `MXC_OK`、退出 0。使用补正前的入口，外层为基础 Python，内层确实启动项目 `.venv`，并在 `step1-git` 下发现和执行 204 项。结果为 203 项通过、1 项跳过，耗时 32.034 秒，子命令退出 0。外层只从 stderr 查选型，因为没有标记，最终输出 `observed_backend=unknown stage=mxc_start_unverified`、退出 1。

随后用短探针查看正在运行的同一子进程链。从内层 Python 向上，依次看到虚拟环境启动器、带专用参数 `--__codex-windows-mxc` 的 Codex 辅助进程和本次外层 Codex。该探针证实可用进程链取证，未用于回填此前的 204 项运行。[取舍与形成依据](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#15-事项-3-的后端取证补正)

### 7.2 每处改动与原因

| 代码位置 | 改动 | 为什么这样改 |
|---|---|---|
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 内层入口 | 保留解释器路径，增加立即刷新的 `MXC_UNIT_INNER_PID`；回环成功明确输出 `MXC_UNIT_LOOPBACK_OK=1` | PID 用于在仍存活时查询实际执行者。明确的成功标记能区分回环已通过与流程中途结束。回环仍由执行套件的同一内层进程探测 |
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 子命令启动 | 用 `Popen` 逐行处理 stdout，stderr 暂存到本次夹具目录，结束后回显 | 原 `subprocess.run` 在子命令退出后才返回，此时进程链可能已不存在。stderr 单独暂存使外层查询身份时无需等待第二条输出管道 |
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 取证 | 宿主通过 CIM 查询内层 PID 的祖先，核对同一 CLI 根 PID、辅助进程的可执行路径和完整专用参数。检查链路连通、循环及创建时间顺序，输出关联的三个 PID | 将观察限制在同一次运行，排除无关进程以及父 PID 被重新分配的明显情况。诊断可定位辅助进程，同时无需输出完整进程命令行 |
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 后端判定 | 保留本次 stderr 标记，缺少标记时接受已关联的进程链，输出 `backend_evidence_source`。冲突或证据不可用时为 `unknown` | 保持原证据来源的兼容，并解决该版本显式 `sandbox` 调用未提供 stderr 选型标记的问题。用户请求的后端和共享日志时间均不足以回填同次结果 |
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 完成判定 | 在零退出码之外，校验实际解释器、明确回环成功、发现数和执行数一致，以及跳过计数和对应原因。识别回环失败并保留已跑用例数与原退出码 | 防止流程提前结束、空套件和不完整输出被标记为通过。判据用实际发现数量，随套件规模变化 |
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) 宿主准备与清理 | 前置准备捕获 `OSError`，保留原错误并输出 `fixture_prepare_failed`。注册临时文件与目录联接清理，先移除联接再清理外层目录 | 上次硬链接拒绝发生在启动沙箱测试命令前，用独立阶段表达。任一步失败时仍清理已经创建的宿主资源 |
| [test_mxc_unit_runner.py](../../tests/unit/test_mxc_unit_runner.py) | 新增受控分类回归，覆盖缺回执、计数与跳过不一致、后端冲突、错误祖先链和时间倒序 | 让正反例可重复，防止下游把请求值或零退出码当成独立证据。真实 MXC 的运行结果另由宿主实测给出 |

### 7.3 复验结果

| 复验 | 结果与意义 |
|---|---|
| 专项分类回归 | `tests.unit.test_mxc_unit_runner` 的 17 项全部通过。真实故障难以稳定触发的分支用受控输入检验，覆盖回环不满足与真实测试失败时不同的阶段，以及缺后端或内层启动证据时的降级 |
| CLI 无效 | 对确认不存在的可执行路径运行实际入口，得到 `cli=unknown stage=cli_invalid`，退出 1 |
| 夹具准备失败 | 在拒绝创建硬链接的执行环境运行实际入口，仍收到原 `WinError 5`，并明确输出 `stage=fixture_prepare_failed`、退出 1。该次运行新建的临时目录清理完毕 |
| 真实 MXC 正例 | 2026-11-23 23:22–23:23，从普通 Windows 宿主用基础 Python 启动，显式选定 Codex `0.167.0-alpha.3.2` 和 `step1-git`，内层为项目 `.venv`。活体进程链与内层回环在同一次运行确认。发现、执行均为 221 项，220 项通过、1 项跳过，耗时 29.810 秒 |

真实正例的关键输出如下。额外的 17 项来自此次入口分类回归，跳过理由与前一轮一致。

```text
MXC_UNIT_DISCOVERED=221
MXC_UNIT_LOOPBACK_OK=1
MXC_UNIT_SKIP=explicit live CLI opt-in required
MXC_UNIT_RAN=221 SKIPPED=1
Ran 221 tests in 29.810s
OK (skipped=1)
cli_exit_code=0
observed_backend=mxc backend_evidence_source=process_ancestry stage=passed
```

运行入口自身也退出 0。用例过程中仍打印三条 `_readerthread` 的 `UnicodeDecodeError`，套件汇总未将这些线程异常计为用例失败。本次验收范围是 MXC 入口取证和阶段分类，未修订子用例的文本解码逻辑。受控分类回归给出分支证据，未额外在原生 MXC 环境刻意破坏内层解释器、回环和正式用例。工作台事项 1 的既有后端快照仍以第 4 节为准。

内层进程未能报告 PID 且 stderr 没有后端标记时，入口返回 `mxc_start_unverified`。`inner_not_started` 适用于 MXC 已经有独立证据、内层解释器却缺少有效回执的情况。

以下文件指纹为本次实测版本的 SHA-256。

| 文件 | 已测 SHA-256 |
|---|---|
| [run_mxc_unit.py](../../tests/verify/run_mxc_unit.py) | `743A98FC2737EFDE9B83D0F21FFC1695E8981DA3F20F5F2E9074CCEF3CCE04E1` |
| [test_mxc_unit_runner.py](../../tests/unit/test_mxc_unit_runner.py) | `CBC1BDA1FC01428BFEC7CEE4839D4EF72343862B3C8F5899F39C431BF37AF3CA` |

## 8. 事项 4 的实测

本轮沿用 [REQ-003 1.0](../requirements/DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md) 的事项 4 和[正式设计](../design/DLL_MXC_IMPROVEMENTS_DESIGN.md)第 6 节。先清理已确认的事项 3 临时文件，并复核单测入口未留下临时目录。验收方法与报告形成经过见[设计草稿第 16 节](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#16-事项-4-的验收记录形成)。

### 8.1 真正发起 Grok 调用

用基础 Python 和独立 SQLite 数据库启动临时工作台。工作台走普通新会话、单一 Grok 收件人，使用已有的一次性诊断入口。通过 HTTP 提交任务，从 CLI 流只提取所需工具事件，再按助手消息关联 `execution_runs.config_snapshot.grok_diagnostic`。验证进程独立读取目标文件字节。每次使用新的测试工作区和数据库，原运行实例无需停服。

| 场景 | 实际调用与独立效果 | 工作台诊断及判定 |
|---|---|---|
| 23:31，正常创建 | CLI 流捕获 `workbench_files__create_file` 的 `tool_call`。独立读取的测试文件字节和请求给定值完全一致 | Grok CLI 退出 0，运行 `completed`、`exit_confirmed=1`。快照记录一个实际 MCP 子进程的 PID 与解释器，核对为同一基础 Python 文件 |
| 23:32–23:33，注入 `ImportError` | 只在测试工作区的 MCP 解释器导入 `_ctypes` 时注入错误。复验捕获 `workbench_files__edit_file` 的 `tool_call`，原文件字节完全一致 | 两轮均捕获 `native_import_uncaught`，诊断持久化 `server_failure_stage=native_load`。Grok 主进程仍退出 0、运行 `completed`，表明主进程可以在工具失败后完成当前回复 |
| 注入场景的身份复核 | 首次运行的身份检查误用了正常创建时的唯一 PID 判据。复验在清理前只读核对原始身份事件，确认有两个不同 PID，两个解释器均与宿主使用同一基础 Python | `server_process=unknown` 符合现有多身份规则。没有证据能把故障关联到其中某个确定 PID |

两个实测场景均确认运行诊断原始文件已清理。客户端调试摘要未采到版本或注册阶段字段。操作判定依据是 CLI 的实际工具调用事件和宿主独立文件检查。

### 8.2 独立 stdio 复验

另用基础 Python 启动真实 [MCP 子进程](../../workbench/adapters/workspace_files.py)，逐个发送 `initialize`、`tools/list` 和 `tools/call`，把同次事件交给[工作台诊断解析器](../../workbench/adapters/grok_diagnostics.py)。每例均核对事件记录的 PID 和启动 PID 相同，解释器文件相同。故障通过探针注入，Windows DLL、工作台实现和用户配置均未改动。

| 场景 | 响应、退出和文件状态 |
|---|---|
| 同一文件创建、编辑、删除，分别走 stdio | 三个调用均为 `isError=false`，子进程均退出 0。独立检查先读到指定初始内容，再读到新内容，最后确认文件不存在 |
| `_ctypes` 导入抛出 `ImportError` | 无 `tools/call` 响应，MCP 子进程退出 1，记录 `native_import_uncaught` 并分类 `native_load`，原文件未变 |
| `_ctypes` 导入抛出 `OSError` | 收到 `isError=true`，MCP 子进程退出 0，记录 `native_os_error` 并分类 `native_load`，原文件未变 |
| 请求访问工作区外文件 | 收到 `isError=true`，MCP 子进程退出 0，没有 `native_load` 诊断，隔离区内的工作区外哨兵文件未变 |

真实 Grok 正例完成的是创建，编辑与删除的成功路径由真实 stdio 服务验证。两类故障的退出码来自独立 MCP 子进程。真实工作台的负例以诊断阶段和文件内容判定，Grok 主进程的退出码单独解读。

## 9. 事项 1 的工作台后端采集补正

依据 [REQ-003 1.0](../requirements/DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md) 的事项 1 与[正式设计](../design/DLL_MXC_IMPROVEMENTS_DESIGN.md)第 6 节。设计取舍及实施前记录见[设计草稿第 17 节](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#17-事项-1-的工作台后端采集补正)。

### 9.1 改动与原因

| 代码位置 | 改动 | 为什么这样改 |
|---|---|---|
| [managed_process.py](../../workbench/execution/managed_process.py) | 用持有的 Windows 进程句柄读取真实的创建 FILETIME，与启动后的 PID 配对 | PID 可能复用。使用同一个进程句柄取得的时间确认 Windows 查询看到的是本次 CLI |
| [codex_diagnostics.py](../../workbench/adapters/codex_diagnostics.py) 实时采集 | 启动只读观察，先确认 CLI 的创建时间和映像，再查询同一 CLI 的直接 MXC 辅助进程和它的工具子进程。核对专用参数与创建顺序，再确认辅助进程身份。仅输出 PID 和创建时间等固定字段 | 从宿主直接取得本次命令的证据。要求看到工具子进程，避免只看到辅助进程启动就推断工具实际由它执行。固定字段避免保存其他进程命令行 |
| [codex_diagnostics.py](../../workbench/adapters/codex_diagnostics.py) 后端判定与退出 | 保留同次 stderr，缺标记时采用已确认的进程链。有冲突、进程退出太快或查询失败时保留 `unknown`。按本次运行回收观察进程，等待有界 | 兼容已有日志来源，并防止配置或其他会话的证据回填。观察任务只用于诊断，有界收尾使缺证据时业务仍能继续 |
| [web_runs.py](../../workbench/execution/web_runs.py) | 用现有 `after_resume` 钩子开启观察，合并已有观察回调。受管 CLI 返回后，由原执行线程合并结果，带同一执行 token 写入 | 避免改动启动时的权限、Job 归属和取消流程，也避免执行域释放后再写入已结束的运行 |
| [execution_tracking.py](../../workbench/execution/execution_tracking.py) | 沿用原快照和归属检查，把 `observed_backend`、`backend_evidence_source` 与可选的 `backend_process_evidence` 在同一事务更新 | 让结论可以追溯到同一条运行证据，同时保留旧记录和原字段，避免另建一个难以关联的全局日志 |
| [test_codex_backend_observation.py](../../tests/unit/test_codex_backend_observation.py) | 加入 12 项受控测试。覆盖日志与进程分歧、无证据、错误根身份、观察器回收，以及独立 SQLite 中的运行归属、权限快照、Stop、超时和诊断写入失败 | 固定正例与反例各自的判据，验证诊断附加信息不会把原任务结果改写成成功或失败 |

### 9.2 同一次真实工作台运行

2026-11-23 23:57，使用普通 Windows 宿主上的基础 Python、独立实例和 SQLite 测试库。显式指向已验收的 Codex CLI。通过工作台消息 API 触发单一 Codex 的普通新会话，执行一条含短时等待的只读 `Get-Location`。CLI JSON 中的实际命令退出 0，输出匹配项目目录。按助手消息 ID 和对话 ID 查询 `execution_runs`，读取该次运行快照。

| 证据 | 实际值 |
|---|---|
| 消息与运行 | 两者均为 `completed`，`exit_confirmed=1` |
| 启动 | `launch_mode=new`，`cli_version=0.167.0-alpha.3.2`。快照的可执行文件与显式指定的 CLI 为同一文件 |
| 权限 | `permission_mode=default`，`permission_profile=step1-git` |
| 后端 | `observed_backend=mxc`，`backend_evidence_source=process_ancestry` |
| 同次证据 | `backend_process_evidence` 含实际 CLI、MXC 辅助进程、工具子进程的三个 PID 及创建时间。顺序为 CLI 创建、helper 创建、工具进程创建 |

这次独立实例和工作区内的临时目录均完成清理。该正例覆盖工作台自己的采集和写库链路，历史三次调用保留其各自快照。

### 9.3 回归和覆盖范围

专项测试 12 项通过。联跑上述专项、MXC 入口、执行跟踪、执行接线与进程托管 5 个模块，共 72 项通过。首次联跑碰到当前执行环境默认临时目录拒绝创建测试数据库。使用项目内的测试专用临时目录后通过。

最终用第 3 项入口从基础 Python 启动内层项目虚拟环境，在真实 MXC 下重跑全量单测。回环成功，发现并执行 233 项，232 项通过、1 项按配置跳过，耗时 35.066 秒。入口实际退出 0，报告 `observed_backend=mxc`、`backend_evidence_source=process_ancestry` 和 `stage=passed`。第 7 节已记录的三条子进程解码线程异常在本次仍出现，套件汇总未将它们计为失败。

0.4 阶段已在真实工作台核对普通新会话、`step1-git` 和显式 CLI。默认 `workspace-write`、续接、只读与冲突的新增采集链当时由受控回归覆盖。其后的真实工作台对照见第 9.4、9.5 节。观察启动失败或结果不明时保持 `unknown`。受管 CLI 的 `exit_confirmed` 仅描述所属 Job 的退出。独立观察进程若遭遇系统拒绝终止或回收超时，仍需独立证据确认它退出。

| 最终验收文件 | SHA-256 |
|---|---|
| [codex_diagnostics.py](../../workbench/adapters/codex_diagnostics.py) | `BB0B298B54CE7C13DA5E67FF82FE20B7BCB25A9C299901D71D47A9FAF533B830` |
| [managed_process.py](../../workbench/execution/managed_process.py) | `6C7FD3F042D112647EBE50FAC4C0636E91B4DEE169DF4503FC29CCC93F498557` |
| [web_runs.py](../../workbench/execution/web_runs.py) | `5E58D99584E23848116C7A185A5768971CD33CFF1895E8598CA28DBC6771B2E5` |
| [execution_tracking.py](../../workbench/execution/execution_tracking.py) | `81DCB34F5752B468BA217D189DED86854C2323FAE4883EC8A5AE86FBD5E8A7A1` |
| [test_codex_backend_observation.py](../../tests/unit/test_codex_backend_observation.py) | `71F9D2557F2567E1725B081E45358D77343C34401A94A11B3C49C2E81F42F180` |

### 9.4 重启后的主工作台复验

2026-11-24 00:06:58 +08:00，用户在重启后的主工作台（端口 `8765`）通过消息 API 向 Codex 发送一次普通新会话请求，要求执行只读 `Get-Location` 并短时等待。助手回复写入时间为 00:07:32 +08:00，来自回复落库时更新的 `workbench_messages.created_at`。用户提供的终端输出显示 Codex 返回了正确的项目工作目录，并报告命令执行成功。[消息写入代码](../../workbench/storage/web_store.py)

按用户提供的助手消息 ID 和运行 ID 只读复核主工作台数据库，核对同一条运行。

| 证据 | 实际值 |
|---|---|
| 运行归属 | `agent_id=codex`，`source_kind=workbench`，`launch_mode=new` |
| 完成情况 | 助手消息与运行均为 `completed`，`exit_confirmed=1` |
| 版本与权限 | `cli_version=0.167.0-alpha.3.2`，`permission_mode=default`，`permission_profile=step1-git` |
| 后端和来源 | `observed_backend=mxc`，`backend_evidence_source=process_ancestry` |
| 同次进程证据 | 快照同时保存了 CLI、MXC 辅助进程、工具子进程的 PID 及各自创建时间。三个 PID 各不相同，创建时间按上述进程链严格递增 |

**通过。** 主工作台重启后的普通新会话已证实使用新增的采集逻辑。此次复核只读查询该记录。续接和其余对照见第 9.5 节。[复验记录形成](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#主工作台重启后的验收记录形成)

### 9.5 续接、其他目录与 CLI 覆盖对照

2026-11-24，用户从普通 PowerShell 用基础 Python 执行分项验收。普通消息经 `POST /api/messages` 投递给 Codex，请求执行 `Get-Location` 并等待约六秒，给同次进程链留出采样时间。脚本按助手消息和对话关联 `execution_runs`，以只读方式取得完成状态、退出确认及配置快照。下述命令结果来自运行时回复，后端及权限来自对应快照。[消息入口](../../workbench/web/routes.py) · [快照记录](../../workbench/execution/web_runs.py)

#### 同一会话续接通过

1. 使用主工作台上第 9.4 节的新建对话。验收入口在发送前后读取已保存的 Codex 线程，确认线程标识一致，输出 `PASS [resume]`。
2. 根据用户提供的前后两条助手消息，在主工作台数据库只读复核它们属于同一对话。前一条记录为 `launch_mode=new`，续接请求发送时间为 00:23:04 +08:00，助手回复写入时间为 00:24:00 +08:00。本轮回复报告命令执行成功，目录仍为项目根。
3. 本轮消息和运行均为 `completed`，`exit_confirmed=1`，`launch_mode=resume`，`permission_mode=default`，`permission_profile=step1-git`，`cli_version=0.167.0-alpha.3.2`。快照记录 `observed_backend=mxc` 和 `backend_evidence_source=process_ancestry`，三个进程 PID 独立且创建时间递增。结论来自续接当轮的证据。[会话绑定与执行入口](../../workbench/execution/web_runs.py)

#### 其他目录的默认权限路径通过

1. 启动独立实例，在临时目录准备 `AGENTS.md` 和 `specs/README.md`。通过 `POST /api/settings` 只给新测试对话指定这个目录，再发送普通新会话的只读检查。
2. 用户实跑输出显示，回复与快照中的工作目录均为指定目录，消息和运行完成且退出已确认。快照为 `launch_mode=new`、`permission_mode=default`、`sandbox=workspace-write`、`permission_profile=null`。由此确认其他目录走了当前工作台的默认权限分支。[目录设置](../../workbench/web/routes.py) · [Codex 权限分支](../../workbench/adapters/web_backends.py)
3. 此例的 `observed_backend` 和 `backend_evidence_source` 均为 `unknown`，输出中的进程证据为 `null`。脚本在后端判据处给出 `FAILED`。本节确认目录设置、真实调用及其权限参数通过，实际执行后端仍待取证。

#### 显式 CLI 覆盖 A/B 通过

两个独立服务使用同一个项目根作为工作目录，分别以普通新会话执行相同的只读任务。A 启动时为服务进程显式设置 `CODEX_CLI`。B 启动时只从该服务进程环境中移除 `CODEX_CLI`。每轮回读 `executable`，与预期可执行文件比较文件身份，再独立执行预期 CLI 的 `--version` 核对 `cli_version`。[CLI 解析规则](../../workbench/adapters/web_backends.py)

| 轮次 | 预期来源 | 实际证据 |
|---|---|---|
| A | 显式指定的 CLI 文件 | 快照路径与该文件匹配，版本核对一致 |
| B | 默认解析出的 CLI 文件 | 快照路径与默认解析结果匹配，版本核对一致；默认解析的具体命中来源未独立取证 |

两轮实际命中同一个 Codex 可执行文件，版本均为 `0.167.0-alpha.3.2`。两轮均为 `launch_mode=new`、`permission_profile=step1-git`，消息与运行均为 `completed`，`exit_confirmed=1`，并分别记录 `mxc / process_ancestry`。终端输出 `PASS [cli-ab]`。两轮的判据是实际文件和版本分别符合各自选择规则。

本轮三个实测的后端正例都有独立 PID、各自的 FILETIME 和实际工具子进程。下表从快照中三个创建时间换算为北京时间并保留毫秒，表示进程创建时刻。

| 本次运行 | CLI 创建 | MXC 辅助进程创建 | 工具子进程创建 |
|---|---|---|---|
| 主工作台续接 | 00:23:04.372 | 00:23:35.780 | 00:23:35.787 |
| 显式 CLI | 00:27:34.519 | 00:27:49.395 | 00:27:49.403 |
| 默认解析 CLI | 00:28:11.603 | 00:28:36.742 | 00:28:36.750 |

续接证据已复核主库。CLI A/B 与换目录场景的记录来自用户实跑输出。截至该轮归档，剩余证据为其他目录的实际后端和新版只读最终汇总。该轮只读用例停在 `waiting_user`。后续独立汇总实测见第 9.6 节。[复验记录形成](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#续接其他目录与-cli-对照的复验记录形成)

### 9.6 新版只读最终汇总实测

2026-11-24 在主工作台发起 `FINAL-RO-VERIFY`。给定材料为 Ubuntu WSL 2 的迁移屏障回归 13 项通过。用户要求入口先问验收编号并等待。随后用户在任务卡选择 **按当前成果给结论**，最终交付汇总给定结果并注明编号尚未提供。以下时间由主库消息的 UTC 时间换算为北京时间。

| 时间 | 步骤 | 同次证据 |
|---|---|---|
| 19:17:55 | 入口回复，消息 `4471` | 返回 `waiting_user` 并带 `plan.tasks=[]`。入口协议拒绝空列表，工作台消息和运行标记 `failed`，没有对应已登记任务 |
| 19:18:20 | 用户纠错，消息 `4472` | 重试请求只描述入口错误。`FINAL-RO-VERIFY` 出现在重试后的计划与回复中 |
| 19:18:41 | 纠错后的入口回复，消息 `4473` | 注册任务 `entry-4473`，负责人和最终答复者均为 `codex`，计划含一项任务。入口调用 `completed`，协议状态 `waiting_user`。随后 `cancel_then_finalize` 事件落为 `done` |
| 19:19:05 | 独立最终汇总，消息 `4474` | 角色 `final`、Agent `codex`。消息、协作调用、运行和任务均为 `completed`，已有最终交付文本，`exit_confirmed=1`、`cancel_requested=0` |

入口在 `waiting_user` 时可省略 `plan`。若提交计划，`tasks` 必须至少有一项。最初 SQL 只在 `collab_jobs.request` 找标识，因此漏掉这次重试任务。改从最终回复的标识和时间定位消息 `4474`，用 `execution_runs.source_message_id` 及对话关联运行，再用任务和调用标识关联 `collab_calls` 和任务生命周期。[入口协议](../../workbench/collaboration/collaboration_protocol.py) · [最终调度与权限](../../workbench/workers/dispatch.py)

| 核对字段 | 实际值 |
|---|---|
| 任务、调用、运行 | `entry-4473`、`6ae87b6b6e234247b50f61425de0cf4a`、`b1f65e47079848a48d38475f2cc3bdc8` |
| 来源与启动 | `source_kind=collaboration`，`launch_mode=resume`，同一默认对话 |
| CLI 身份 | 快照含实际 `executable`，为用户安装目录下 `OpenAI/Codex/bin/5449c1f0f26f1f98/codex.exe`，版本 `0.167.0-alpha.3.2` |
| 权限参数 | `permission_mode=read-only`，`sandbox=read-only`，`permission_profile=null` |
| 后端与来源 | `observed_backend=unknown`，`backend_evidence_source=unknown`，无同次进程链字段 |

**新版只读最终汇总通过。** 记录确认独立最终调用及其只读启动参数。用例按要求只汇总文字，后端按证据记录为 `unknown`，符合 REQ-003 判据。新建和续接的后端正例已单独取得。结合第 9.4、9.5 节，事项 1 的原定验收已完成。[补验形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#新版只读最终汇总的验收记录形成)

## 10. 事项 2、5、6 补验

Windows 工作树基线 `35a0380`。基础 Python 执行 `python -B -m unittest tests.unit.test_database_schema tests.unit.test_managed_process tests.unit.test_capabilities`，44 项通过。项目虚拟环境复跑 Job 模块，18 项通过。临时目录设在项目内。

- **事项 2**。数据库回归覆盖正常获锁、真实跨进程共享／独占互斥和事务调用路径。分别注入 ctypes 导入失败、WinDLL 加载失败和锁文件打开被拒，得到两次 `native load failed` 和一次 `system error`。均拒绝进入临界区、保留异常链并清空文件引用，撤销注入后获锁成功。当时 WSL 返回 `Wsl/E_ACCESSDENIED`，POSIX 尚未执行。安装 Ubuntu 后的复验见第 10.2 节。
- **事项 5**。现有取消测试实测基础 Python 的启动器与解释器同为 PID 35132，项目虚拟环境分别为 38008 和 31672。核对解释器路径及实际 PID 的 Job 归属，取消为 `cancelled / confirmed`，持有的进程句柄均确认退出。模块也覆盖超时清理和禁止回退。取消流程中的 `confirmed` 来自 `_cleanup()` 确认根进程退出且 Job 活动进程数为零。报告所列被测实现与用例的 SHA-256 均已复核。真实系统直接拒绝分支未单独取证。[取消清理实现](../../workbench/execution/managed_process.py) · [双入口用例](../../tests/unit/test_managed_process.py)
- **事项 6**。能力模块覆盖正常探测及受控 ssl 导入失败。失败时 HTTPS 为 `host_native_dependency_failed_ssl`，其余两项通过、一项失败、一项跳过。环境为 `workbench`，Agent 为 `not_tested`。HTTP 入口回归通过，故障注入在探测管理器入口执行。

| 被测文件 | SHA-256 |
|---|---|
| [migration_barrier.py](../../workbench/execution/migration_barrier.py) | 10920f8f465bf60151cd6c749c5c62764ec1adc8910acc569f4f60756e748a54 |
| [managed_process.py](../../workbench/execution/managed_process.py) | 6c7fd3f042d112647ebe50fac4c0636e91b4dee169df4503fc29ccc93f498557 |
| [capability_probe.py](../../workbench/agents/capability_probe.py) | 0e30fe59b098a5a648c440c02316df6e9c70ba2c7c8189ef96b64212c95eea1f |
| [test_database_schema.py](../../tests/unit/test_database_schema.py) | 494c7833bbfd4c98168a7ed71810a6b1b3cfadaf9c98a7acd0f4f7bcb5ef317c |
| [test_managed_process.py](../../tests/unit/test_managed_process.py) | 4f6bb770625d70c9e401d2267be0159d2b2334024b172e7bf84624db07008dab |
| [test_capabilities.py](../../tests/unit/test_capabilities.py) | d2e0c776e65be69ec7c03ac1f040d01842e7c922df56b11850b9406c3a2f5f44 |

[补验形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#18-事项补验归档)

### 10.1 事项 6 的 HTTP 故障复验

2026-11-24 18:28:38 +08:00，使用基础 Python 启动隔离的 Windows 工作台，绑定系统分配的回环端口和独立数据库。准备两个无远端的临时 Git 仓库，用 `POST /api/settings` 分配到不同测试对话，以便两组分别完成探测并读取自己的缓存。

两组均从真实 `POST /api/capabilities/probe` 触发后台探测，HTTP 返回 202。随后通过 `GET /api/capabilities` 轮询相同对话，HTTP 200 返回 `probe.state=completed`。保持现有探测和 HTTP 实现。对照组允许实际导入 `ssl`，在出站 HTTPS 的边界提供受控 HEAD 响应 204。故障组仅在能力探测模块导入 `ssl` 时受控抛出 `ImportError`。两组都执行了真实的临时文件创建、读回、删除和本地 `git status`。[HTTP 路由](../../workbench/web/routes.py) · [探测实现](../../workbench/agents/capability_probe.py)

| 核对项 | 对照组 | 故障组 |
|---|---|---|
| 工作区文件 | `passed / temporary_file_created_read_and_removed` | 同左，实测项目内的探测临时文件已删除 |
| 本地 Git | `passed / repository_readable` | `passed / repository_readable` |
| 两项 HTTPS | `responded`，受控响应状态均为 204 | 两项均为 `failed / host_native_dependency_failed_ssl`，各命中一次注入，出站调用数为 0 |
| 远端 Git | `skipped / origin_unavailable` | `skipped / origin_unavailable` |
| 作用范围 | 五条结果均为 `environment=workbench`，`agent_sandbox.state=not_tested` | 五条结果均为 `environment=workbench`，`agent_sandbox.state=not_tested` |

**现有 HTTP JSON 验收通过。** 对照组的 204 用于核对结果形态，真实公网连通性不在这次证据范围内。当前聊天页面没有探测结果展示，本次核对的是同一响应中宿主检查和 Agent 状态的边界。隔离服务、测试数据库和目录已经清理。[复验形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#事项-6-的-http-同次故障复验)

### 10.2 事项 2 的 WSL POSIX 回归

在 WSL 2 的 Ubuntu 26.04.1 LTS 中执行。内核为 `6.6.87.2-microsoft-standard-WSL2`，Linux Python `3.14.4`，`os.name=posix`，SQLite `3.46.1`。从当前工作树读取源码，测试库和锁文件位于 Linux 文件系统的独立临时目录。测试依赖按 [requirements.txt](../../requirements.txt) 安装到临时 Linux 虚拟环境，实际 MCP 版本为 `1.30.0`。

使用该虚拟环境的 Python 在项目根目录执行 `python -B -m unittest -v tests.unit.test_database_schema`。**13 项全部通过，零失败、零错误、零跳过，耗时 1.226 秒，退出码为 `0`。** 覆盖真实跨进程互斥、Web 和 MCP 桥事务持锁、迁移备份与恢复、幂等、分阶段回滚和旧会话映射。旧会话审阅警告来自用例自建的旧库数据。

| 场景 | 补充探针实际结果 |
|---|---|
| 父进程持共享锁，子进程申请共享锁 | 成功获锁和释放，子进程退出 `0` |
| 父进程持共享锁，子进程申请独占锁 | 返回 `lock contention; retry after active transactions/migration finish`，异常链保留 `BlockingIOError`（`EAGAIN=11`），文件引用清空 |
| 父进程持独占锁，子进程申请共享锁或独占锁 | 两次均返回相同竞争分类和原始错误，各自文件引用清空 |
| 父进程释放锁，子进程再次申请独占锁 | 成功获锁和释放，子进程退出 `0` |
| 在预期锁文件的位置预置目录，再打开锁文件 | 实际 `os.open` 返回 `IsADirectoryError`（`EISDIR=21`），外层为 `system error`，保留原始异常，未进入临界区，文件引用为空 |

竞争探针核对完整错误文本，三个竞争子进程按探针约定退出 `3`，汇总探针退出 `0`。另回查了源码调用方，原锁竞争提示文本未被产品调用方用作分支，现有跨进程用例按子进程退出码判断，事务持锁用例按异常类型判断。

| WSL 读取的被测文件 | SHA-256 |
|---|---|
| [migration_barrier.py](../../workbench/execution/migration_barrier.py) | `10920f8f465bf60151cd6c749c5c62764ec1adc8910acc569f4f60756e748a54` |
| [service_instance.py](../../workbench/execution/service_instance.py) | `c7094c534230776e2572dce378cb2baa54046a5b0ba6b4ae0f211fdc9b739776` |
| [database_schema.py](../../workbench/storage/database_schema.py) | `cd39e27a5a94d3661be124d77e38ac3f04ad04c86a253382284894f003fc5358` |
| [test_database_schema.py](../../tests/unit/test_database_schema.py) | `494c7833bbfd4c98168a7ed71810a6b1b3cfadaf9c98a7acd0f4f7bcb5ef317c` |

工作树基线为 `35a0380`，屏障和现有测试模块的指纹与第 10 节 Windows 证据一致。测试及探针均已清理临时虚拟环境、数据库及锁文件。本次结论覆盖事项 2 的 Linux POSIX 迁移屏障和数据库调用路径。[补验形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#事项-2-的-wsl-posix-实测形成)

## 11. 收尾结论与复验边界

六项均按 REQ-003 1.0 与正式设计 1.0 的主要判据完成工作树级验收。结论依据第 1 节汇总及各节的同次实测、受控故障验证和源码指纹。后续与其他环境或版本比较时，以各节记录的被测对象为准。[收尾形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#19-六项改进正式收尾形成记录)

| 范围 | 仍需保留的证据边界 |
|---|---|
| Codex 后端 | 新建、续接和 CLI 覆盖 A/B 取得同次 MXC 证据。只读最终汇总及其他目录样本的实际后端为 `unknown`。其他目录的默认权限和只读汇总的启动参数均已有对应快照 |
| POSIX 迁移 | Linux 实测环境为 Ubuntu WSL 2，正常获锁、跨进程共享／独占和错误分类以第 10.2 节范围为准 |
| MXC 单测入口 | 部分诊断阶段采用受控回归。233 项全量运行有 1 项按配置跳过，并打印三条 `_readerthread` 解码异常，套件汇总未计为失败 |
| Grok 文件工具 | 工作台正例覆盖 MCP 创建，原生故障复验覆盖编辑请求和文件不变。独立 stdio 覆盖其余通道。故障同次采到两个子进程 PID，单一身份标 `unknown` |
| Windows Job | 两组真实入口均确认取消、句柄退出和 Job 清空。系统直接拒绝分支未单独取证 |
| 能力探测 | 对照组 HTTPS 204 为受控响应。正式验收范围为实际 HTTP JSON 链路及字段含义，公网上游、页面渲染和 Agent 沙箱能力维持未测 |

这次收尾对应已记录的工作树、环境和场景。代码审核及合入以对应分支和批准记录为准。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-24 19:28:26 +08:00 | 1.0／六项正式收尾 | 0.10 分项验证→正式验收记录 | 六项按原判据核对，复验边界见第 11 节。本机时钟 | 用户确认收尾，Codex 整理 | 验证报告与关联入口 |
| 2026-11-24 19:23:00 +08:00 | 0.10／事项 1 新版只读最终汇总 | 新版等待入口→独立只读汇总交付及快照通过 | 同一任务、调用、消息和运行的主库证据，见第 9.6 节。本机时钟 | 用户实跑，Codex 核对和整理 | 事项 1 的验证报告与索引 |
| 2026-11-24 19:12:22 +08:00 | 0.9／事项 2 WSL POSIX 回归 | Windows 分类与互斥通过、POSIX 待验→两端验证通过 | Ubuntu 实跑 13 项及真实跨进程错误探针，见第 10.2 节。本机时钟 | 用户要求继续，Codex 实测和整理 | 事项 2 的验证报告及索引 |
| 2026-11-24 18:30:02 +08:00 | 0.8／事项 6 HTTP 故障复验 | 探测和路由分别验证→同次故障回归经实际 HTTP 通过 | 隔离实例的正常和故障对照见第 10.1 节。本机时钟 | 用户要求核对，Codex 验证和整理 | 事项 6 的报告及索引 |
| 2026-11-24 00:35:43 +08:00 | 0.7／事项 1 通过项补录 | 续接、其他目录权限及 CLI 对照待补→归档本轮通过部分 | 用户真实验收，复核主库续接快照，见第 9.5 节。本机时钟 | 用户实测并要求详细记录，Codex 核对整理 | 事项 1 的报告与索引 |
| 2026-11-24 00:14:14+08:00 | 事项 2、5、6 补验 | 待补验→记录结果 | Windows 实跑及受控注入，见报告第 10 节。本机时钟 | 用户授权，Codex 验证与归档 | 补验记录 |
| 2026-11-24 00:10:04 +08:00 | 0.5／主工作台重启复验 | 隔离实例正例通过→追加主工作台正例 | 用户终端输出与同次运行快照一致，见第 9.4 节。本机时钟 | 用户实测，Codex 只读核对并记录 | 事项 1 的主工作台验证、报告及索引 |
| 2026-11-24 00:00:49 +08:00 | 0.4／全量回归补录 | 专项及执行链回归→追加真实 MXC 全量复跑 | 最终版本执行 233 项，见第 9.3 节。本机时钟 | Codex 验证和记录 | 事项 1 补正后的全量回归 |
| 2026-11-23 23:59:31 +08:00 | 0.4／事项 1 正例补正 | 工作台功能通过、后端正例缺失→真实工作台采集正例通过 | 用户授权实现并逐项记录原因。真实运行与受控回归见第 9 节。本机时钟 | 用户授权，Codex 实施和记录 | 工作台采集、专项测试、验证报告和索引 |
| 2026-11-23 23:34:07 +08:00 | 0.3／事项 4 验证 | 未实测→通过隔离真实调用及故障返回契约 | 真实工具事件、独立文件核对、解释器身份和原生失败诊断，见第 8 节。本机时钟 | 用户授权，Codex 验证和记录 | 本报告及验证索引 |
| 2026-11-23 23:23:52 +08:00 | 0.2／事项 3 补正验证 | 前置受阻及未取证→追加真实 MXC 成功和分类证据 | 用户授权修改并逐点记录原因。见第 7 节。本机时钟 | 用户授权，Codex 实施和记录 | 单测入口、分类回归及本报告 |
| 2026-11-23 22:55:44 +08:00 | 0.1／复盘后续六项改进验证 | 无→新增分项验证记录 | 用户要求独立保存既有验收过程、实际字段和边界。形成依据见 [设计草稿第 14 节](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#14-后续验证记录形成)，本机时钟 | 用户提出，Codex 整理 | 本报告、形成记录及验证索引 |
