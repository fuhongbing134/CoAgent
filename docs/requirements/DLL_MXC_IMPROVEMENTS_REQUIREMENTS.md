# DLL/MXC 复盘后续 6 项改进需求分析

| 字段 | 值 |
|---|---|
| 编号 | REQ-003 |
| 版本 | 1.0 |
| 状态 | 正式需求分析基线。六项主要验收已完成，见[正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md) |
| 形成依据 | DLL/MXC 教训 [docs/lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md](../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md) §4 六项后续事项；entry-4333 用户要求继续分析、参考现有需求分析文档格式、三方达成一致；entry-4358 用户确认正式落地。草稿基线见 [docs/drafts/DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md](../drafts/DLL_MXC_IMPROVEMENTS_REQUIREMENTS.md) 0.1 |
| 代码基线 | `35a0380` |
| 更新 | Codex，2026-11-24 19:28:26 +08:00（本机时钟）。本次为验收状态归档 |

## 1. 背景与目的

本机工作台 Windows 启动链故障经切换沙箱后端到 MXC 修复。复盘留下的 6 项改进都是「验证／诊断／错误分类」层面，不改变权限、锁、Job、探测的现有语义。目的：让后续每次调用、迁移、测试入口、文件工具、进程管理与能力探测都能把结论绑定到「实际运行的对象、环境和阶段」，证据缺失时如实保留未知或未测，避免再次出现「无法确认实际沙箱后端」「原生加载失败被误报为等锁重试」这类掩盖真因的情况。

## 2. 可行性与接纳结论

**六项均已接纳。** 均为诊断与验证边界收紧，改动集中在记录字段、错误分层、验收入口与表达边界，不触及安全收紧、锁语义、Job 语义或探测语义。接纳时的可行性依据来自 DLL/MXC 教训第 4 节与三方源码核对。后续实施和动态验收见[正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md)。

## 3. 接纳场景（改进项）

下列现状、源码行号和未知项记录需求接纳时的基线。验收范围和成功判据沿用本节。

### 事项 1（高）Codex CLI 身份与观测后端 `workbench/adapters/web_backends.py:136`

- **现状与触发**：`diagnostic_metadata`（第 134–144 行）中 `permission_profile_requested` 仅在 `step1-git` 有名字，只读与默认 `--sandbox CODEX_SANDBOX` 都落 `None`，无法区分；该字段也无法证明后端生效。`web_runs.py:251–263` 启动快照已有解析后的 `executable`（不是完全缺失），带 `--sandbox` 时另记请求的 `sandbox`，仍是请求值。缺口是 **CLI 版本、独立观测到的后端及其证据来源**（历史验收用过「Windows sandbox selection: mxc」）。
- **目的**：每次 Codex 调用可追溯「实际 CLI 可执行文件 + 版本 + 请求的权限模式/配置名 + 独立观测到的后端及证据来源」。
- **范围边界**：结构化记录权限模式与配置名（不强制记录权限参数原文）；记录 CLI 路径（可复用快照的 `executable`）、版本、观测后端及证据来源；CLI 解析、版本、后端可分别标 `unknown`；覆盖新建、续接、只读汇总，并覆盖命名配置与默认路径。基础 Python 与 `.venv` 对照留给事项 3、5。
- **约束**：不改权限参数生成逻辑与安全收紧语义；「请求的权限」与「独立观测到的后端」不得混淆；观测须有独立证据来源，不得臆测；不为观测后端放宽沙箱。
- **成功判据**：新建、续接、只读汇总各跑一次，诊断记录含 CLI 路径、版本、权限模式与配置名、观测后端或 `unknown` 及证据来源；新增字段向后兼容，不破坏既有 consumer。
- **未知**：CLI 版本获取方式；观测后端的稳定来源。
- **优先级与依赖**：高；可与事项 3、5 对齐「实际解释器/CLI」表达，独立验收。

### 事项 2（高）迁移屏障错误分类 `workbench/execution/migration_barrier.py:23–43`

- **现状与触发**：`__enter__` 内 `import ctypes`（第 24 行）、`WinDLL('kernel32')`（第 30 行）、`LockFileEx`（第 33 行）；第 40–43 行 `except BaseException` 把一切异常统一包装为「Migration barrier unavailable; retry after…」。原生加载失败被误报为锁竞争。
- **目的**：按失败阶段区分「原生加载失败／真实锁竞争／非竞争系统错误」，失败可定位、可操作。
- **范围边界**：按失败阶段分三类，`ctypes` 导入失败与 `WinDLL` 加载失败是同一类（原生加载）的两个触发点；只有确认锁竞争（如 `ERROR_LOCK_VIOLATION`）才提示等待重试；非竞争系统错误单列。
- **约束**：三类失败都拒绝进入临界区、保留 `from error` 异常链、清理已打开资源；不改锁语义（独占/共享）、`LockFileEx` 参数、无 TTL 接管、文件生命周期；POSIX 路径与正常获锁行为不变。
- **成功判据**：注入「原生加载失败」「真实锁竞争」「非竞争系统错误」分别得到可区分消息；正常获锁与 POSIX 回归通过；交付前核对现有调用方对异常类型及文本的依赖（仓内仅 `migration_barrier.py:43` 匹配该英文提示，`test_database_schema.py:144–154` 只断言子进程退出码）。
- **未知**：调用方对异常文本的隐式依赖。
- **优先级与依赖**：高；依赖对调用方异常契约的核对。

### 事项 3（高）MXC 单测入口运行前提 `tests/verify/run_mxc_unit.py:59–82`

- **现状与触发**：默认 PATH `codex`、内层 `.venv`、权限配置 `:workspace`（无回环权限）；历史出现两种失败——外层由 `.venv` 启动报 MXC 不可用、默认权限下完整套件六项回环错误，须分别归因。
- **目的**：从一次输出判断外层/内层解释器、实际选中 CLI、请求配置、发现数、跳过原因、退出码，并定位失败阶段。
- **范围边界**：覆盖完整套件入口的运行身份、回环前提、阶段诊断与结果汇总；回环证据必须来自同一 MXC 执行上下文；CLI 路径/版本指向本次选中对象，取不到标未知。
- **约束**：保留显式 CLI/解释器/权限覆盖；沿用权限边界、真实链接夹具与清理；传播真实退出码；回环未满足时报告该阶段并使完整套件失败，不得静默跳过 HTTP 用例。
- **成功判据**：基础 Python 启动外层 + 项目解释器跑内层，输出能关联两层解释器、所选 CLI、请求配置、套件计数/跳过/退出码；分别定位 CLI 无效、MXC 未启动、内层无法启动、回环不满足、普通测试失败；显式 CLI 覆盖验证记录与实际选中一致。历史 202 项只作对照。
- **未知**：当前机器可用权限配置、CLI 版本来源、MXC 启动证据；回环失败能否进一步区分策略拒绝与本机网络故障。
- **优先级与依赖**：高；可与事项 1 对齐 CLI/后端术语，独立验收。

### 事项 4（中）Grok 文件工具实际解释器与原生依赖 `web_backends.py:371` + `workspace_files_windows.py:4`

- **现状与触发**：普通 Grok 运行把 `sys.executable` 写入 `workbench_files` 的 MCP 配置（第 371 行）；配置命令只证明「请求的解释器」，不证明实际子进程身份。`workspace_files.py:135` 的 `tools/call` 捕获 `OSError` 等、**不捕获 `ImportError`**；`workspace_files_windows.py:4` 模块级 `import ctypes` 失败会在 `operate()` 导入时冒出，表现为进程失败而非 `isError`。
- **目的**：查明 MCP 子进程实际解释器，区分服务启动、工具注册、原生依赖加载与文件操作失败。
- **范围边界**：覆盖普通 Grok 路径上已授权的 `workbench_files`；复用客户端注册与服务端诊断；核对 `ImportError` 与 `OSError` 两类加载失败在调用者侧如何呈现。
- **约束**：沿用工作区授权、Windows 句柄相对操作、`.git`/重解析点/多链接防护；原生依赖失败仍拒绝；诊断不带凭据/提示词/文件正文；最终汇总路径维持现有装配边界。
- **成功判据**：真实 MCP 有界文件操作 + 独立核对文件结果 + 该次子进程解释器身份（仅注册事件不算完成）；注入原生加载失败后调用侧能定位该阶段且测试文件不变；路径拒绝保持独立含义；缺子进程身份或调用证据分别标未知/未测。
- **未知**：目标场景宿主解释器、现有诊断能否关联实际 MCP 子进程、能否复用端到端入口。
- **优先级与依赖**：中；表达与事项 6 对齐（宿主结果／工具或 Agent 子进程结果／未测）。

### 事项 5（中）启动器与解释器双进程的 Job 归属、取消、退出 `managed_process.py:220` + `verify_task6_breakaway.py:144`

- **现状与触发**：`_create` 用 `CreateProcessW` 挂起创建 `command[0]` 并只对 `hProcess` 查 `IsProcessInJob`；`.venv` 下启动器与实际解释器曾是两个 PID；`command()` 绑定本次 `sys.executable`，两组入口未在同一次运行里都被覆盖。
- **目的**：证明「本次实际启动器」与「实际执行脚本的解释器」都属对应 ManagedProcess Job，取消后两者退出；一组入口结果不能代替另一组。
- **范围边界**：验收单位是各组入口实际产生的证据——两次独立运行或复用现有入口均可；基础 Python 与项目 `.venv` 分别记录实际启动器与脚本解释器；对每个 PID 查 `IsProcessInJob(handle, owner.job_handle)`；取消后用已确认身份的进程句柄验退出，并核 Job 清空与运行结果。
- **约束**：不放宽 breakaway；「离开内层启动器 Job」不得写成「离开 CoAgent Job」（嵌套 Job）；`dispatch_allowed` 仍仅 `not_started`/`confirmed`；系统直接拒绝的结果单列，其证据只覆盖拒绝路径。
- **成功判据**：两组入口各跑一次，输出含实际解释器路径、启动器 PID、解释器 PID（同进程则给身份依据）；取消后两句柄退出、`outcome=cancelled`、`exit_evidence` 为 `confirmed` 或显式 `unknown`；预期进程身份取不到时报证据不足，不得臆造双 PID。
- **未知**：`.venv` 双 PID 稳定性；`verify_task6` 是否必须兼做本组验收。
- **优先级与依赖**：中；可与事项 3 对齐「报告实际解释器」，验收独立。

### 事项 6（中）宿主探测与 Agent 沙箱结果边界 `workbench/agents/capability_probe.py:7,73`

- **现状与触发**：模块只探测工作台进程（第 1 行）；第 7 行模块级 `import ssl`，`_ssl` 初始化失败发生在任何探测前；第 73 行结果固定 `environment="workbench"`；`/api/capabilities` 把宿主 `probe` 与 `cli_found`（仅 CLI 是否在 PATH）并列。
- **目的**：分清「宿主测到什么」与「Agent 沙箱测到什么（或未测）」；宿主原生依赖失败报原生依赖失败，不得写成 Agent 无能力，也不得把宿主成功当沙箱成功。
- **范围边界**：保留并强化宿主范围标识；能力快照增加 Agent 沙箱字段并固定为未测，禁止回填宿主结果；宿主模块导入失败给出可区分原生依赖失败（至少覆盖 `ssl`）；界面/JSON 在宿主成功、沙箱未测时并列不混淆。`ssl` 延迟导入留给设计，本项不强制。
- **约束**：不新增 Agent 沙箱探测接口；不改同源、Token、非交互 Git、禁止凭据输出；`environment="workbench"` 不得改；未测必须显式；不把 `cli_found` 升级为沙箱能力。
- **成功判据**：正常宿主探测标 `workbench`、Agent 侧未测；注入 `ssl` 加载失败后从真实调用入口验证能定位宿主失败阶段，受影响 HTTPS 探测据实报告、其余探测按实际结果；覆盖「宿主通过/Agent 未测」与「宿主原生失败/Agent 未测」两端文案可区分。
- **未知**：是否已有 Agent 沙箱能力探测接口（本项不引入）；界面渲染位置。
- **优先级与依赖**：中；与事项 2（原生失败分类）、事项 4（宿主 vs 子进程）共用表达约定。

## 4. 价值性

| 风险或问题 | 期望判断 | 能支持的决定 |
|---|---|---|
| 无法证明 MXC 是否生效 | 每次调用能关联实际 CLI、版本、请求权限与观测后端 | 区分配置/CLI 版本/后端问题，复盘有据 |
| 原生加载失败被误报为等锁重试 | 失败阶段可区分 | 定位原生依赖问题，避免无效重试 |
| MXC 单测入口失败不可定位 | 阶段与前提可定位 | 决定是否放行回归证据 |
| Grok 文件工具失败不可定位 | 区分启动/注册/原生加载/操作 | 定位工具链路故障 |
| 双进程 Job 归属未分别验证 | 两组入口各自证据 | 决定 Job 取消/退出是否可信 |
| 宿主与 Agent 能力混淆 | 两端结果边界清晰 | 正确解读能力探测 |

结果应让不熟悉项目的读者看懂：实际运行了什么、哪些结论有证据、哪些标未知或未测、据此采取什么行动。每条结论绑定被测版本、配置和场景，明确通过范围与证据不足。

## 5. 期望产物

- 每项改进后的验证/诊断记录：绑定实际对象（CLI/解释器/PID/子进程）、环境（后端/权限/Job）、阶段（启动/注册/加载/操作/取消）。
- 可复用的错误分类与表达约定：原生加载失败／锁竞争／其他系统错误；宿主结果／工具或 Agent 子进程结果／未测。
- 面向维护者的验收记录：每项成功判据的实测或显式「未测/未知」。
- 本文件保留正式需求分析基线。实施方式见[正式设计](../design/DLL_MXC_IMPROVEMENTS_DESIGN.md)，动态验收和复验边界见[正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md)。

## 6. 参考资料

<details>
<summary>展开参考资料</summary>

- [Windows 虚拟环境 DLL 故障与 MXC 排查教训 1.0](../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md) §4 六项后续事项。
- [DLL/MXC 调查草稿](../drafts/CODEX_WINDOWS_VENV_DLL_MXC_INVESTIGATION.md) §2.1、§7.1、§8.6、§9.1。
- 源码位点：`workbench/adapters/web_backends.py:107–144,371`、`workbench/execution/web_runs.py:251–263`、`workbench/execution/migration_barrier.py:23–43`、`tests/verify/run_mxc_unit.py:59–82`、`workbench/adapters/workspace_files.py:50–56,131–137`、`workspace_files_windows.py:4`、`grok_diagnostics.py:41–112`、`managed_process.py:15,137–226,298–300`、`tests/unit/test_managed_process.py:41–42,204–261`、`capability_probe.py:1,7,35–36,73,157`、`workbench/web/routes.py:23–34`、`tests/unit/test_capabilities.py`、`tests/unit/test_database_schema.py:144–154`。
- 现有需求分析格式：[REQ-001](CONCURRENCY_LOAD_TEST_REQUIREMENTS.md)、[REQ-002](SOURCE_LAYOUT_REQUIREMENTS.md)。

</details>

## 7. 不接纳场景

<details>
<summary>展开本轮不纳入的边界与重评条件</summary>

- 具体实现手段（如何观测后端、如何取 CLI 版本、是否延迟导入 `ssl`）由后续设计确定，本需求只定验收目标。
- 不新增 Agent 沙箱能力探测接口；不把宿主探测或 `cli_found` 升级为沙箱能力。
- 不改权限、锁、Job、探测的现有语义与拒绝行为。
- 需求分析阶段的范围止于接纳与验收约定。真实 MCP 调用、双 PID 复现和原生加载失败注入均由后续验收阶段执行。

</details>

## 8. 形成记录

三方（DeepSeek Harness、Codex、Grok）各析 2 项后交叉检视，逐点收敛结果：

| 议题 | 意见与处理 |
|---|---|
| 事项 1 CLI/后端 | Codex、Grok 均反对「强制记录权限参数原文」，Grok 反对「把 CLI 路径写成完全缺失」（快照已有 `executable`）。采纳：结构化记录权限模式+配置名、CLI 路径复用快照、补版本与观测后端及证据来源。 |
| 事项 2 错误分类 | Codex、Grok 均同意按失败阶段分三类（原生加载/锁竞争/其他），`ctypes` 与 `WinDLL` 为同类两触发，仅锁竞争提示重试。采纳。 |
| 事项 3 单测入口 | Grok 赞同，回环证据须来自同一 MXC 上下文。采纳。 |
| 事项 4 文件工具 | Grok 赞同并补：`tools/call` 不捕获 `ImportError`，原生失败可表现为进程失败。采纳。 |
| 事项 5 Job 验证 | Codex 有条件赞同；Grok 据其意见把验收单位改为「各组入口实际证据」。采纳。 |
| 事项 6 宿主/Agent | Codex 赞同；Grok 据其意见将 `ssl` 延迟导入留给设计。采纳。 |

三方对 6 项均已收敛到一致：均以「每条结论绑定实际对象、环境、阶段；取不到标未知/未测」为共同验收原则。2026-11-23 的需求形成阶段仅做只读分析。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-24 19:28:26 +08:00 | REQ-003 1.0／验收状态归档 | 需求已接纳、验收待开展→六项主要验收已完成 | [正式验收报告](../verification/DLL_MXC_IMPROVEMENTS_VERIFICATION.md)及[收尾形成记录](../drafts/DLL_MXC_IMPROVEMENTS_DESIGN.md#19-六项改进正式收尾形成记录)。本机时钟 | 用户确认收尾，Codex 整理 | 当前状态及验收入口，保留原需求判据 |
| 2026-11-23 08:08:00 +08:00 | 1.0／entry-4358 | 0.1 草稿（三方一致）→正式需求分析基线 | 用户确认正式落地；本机时钟 | 用户确认；DeepSeek Harness 转正 | 6 项改进需求基线，实施与验收待开展 |
| 2026-11-23 08:00:20 +08:00 | 0.1／entry-4333 | 无→需求分析草稿（三方一致） | 三方各析 2 项 + 交叉检视逐点收敛，见 §8；本机时钟 | 用户提出；DeepSeek Harness 汇总 | 6 项改进需求基线；改进仅为建议、尚未授权实施 |
