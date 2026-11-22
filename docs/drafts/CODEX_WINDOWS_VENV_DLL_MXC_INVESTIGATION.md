# Codex 工作台在 Windows 下的 Python 虚拟环境 DLL 故障复盘

| 项目 | 记录 |
|---|---|
| 状态 | 保留 0.1 原始调查及本机验收基线。正式教训见 [../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md](../lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md)，形成依据见 §10 |
| 排查日期 | 2026-11-21。除特意注明外，事件时间均为北京时间 UTC+08:00，时分秒按相应记录精度保留 |
| 涉及系统 | Windows 11 25H2，OS Build `26200.9711`。项目虚拟环境基于 CPython 3.13.7 x64 |
| 现场版本 | `feature/GitDiscussion`。故障现场的当前提交为 `88b3b73751ccfaeaf66402e9f310ee5e64e94ae0`，对照的 `main` 为 `c8f2e0a8c490c6400142707a69967919bd548619`。本次分析使用当时工作台实际调用的 `codex-cli 0.167.0-alpha.3.2` |
| 适用范围 | CoAgent 工作台发起的 Codex 命令、项目 `.venv`、Windows 沙箱实现、本次相关验收与测试改动 |

## 1. 结论与证据链

故障可以在原工作台 Codex 调用的 Windows 沙箱里用单条 `import` 复现。通过项目 `.venv\Scripts\python.exe` 启动时，`_ctypes` 和 `_ssl` 导入失败。同一条命令在相同工作台、相同目录和同一普通任务权限下，直接调用创建虚拟环境的基础 Python 就能成功。因此，起决定作用的差异之一是 **Windows 虚拟环境启动器引出的进程启动路径及其运行上下文**。

ProcMon 确认失败进程也从相同路径打开和映射了扩展模块及其主要依赖。WinDbg 随后在失败进程捕获到 **`USER32.dll` 的 `DLL_PROCESS_ATTACH` 初始化返回 `FALSE`**，线程状态中同时观察到 `STATUS_ACCESS_DENIED`。`_ctypes` 的进一步取证还记录到进程使用 Codex 的私有桌面。已有证据把故障推进到 User32 初始化阶段。确切拒绝访问的 Windows 对象、访问掩码及系统调用仍未定位，两种扩展触发这条初始化链的完整内部调用也未复原。

使用 `windows.sandbox=mxc` 的一次性对照中，原 `.venv` 直接导入 `ctypes`、`ssl` 和项目的 `mcp` 均通过。后续在项目根新增 [.codex/config.toml](../../.codex/config.toml)，工作台实际的 Codex 新建、续接、停止及停止后的续接均通过对应验收。MXC 单测中的临时目录、真实文件链接、进程 Job 行为和句柄探测另有独立的对照及修改理由，见第 6–8 节。

## 2. 初始现象及历史对照

### 2.1 提交及推送后报告的单测失败

2026-11-21 19:35:15，工作台 Codex 在项目目录用续接会话执行以下命令。任务实际使用 `step1-git` 权限配置，审批策略为 `never`，当时的 Windows 沙箱选择为 `elevated`。终端记录中的测试运行发生在该次提交之前。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit
```

结果原样保留关键行。

```text
Ran 81 tests in 1.268s
FAILED (failures=5, errors=68, skipped=1)
exit code: 1
```

用户看到的最初报告有日志截断和中文编码问题。19:37 用同一工作台 `.venv` 启动、改由 `TextTestRunner` 汇总所有失败后，计数仍为 `81 / 5 / 68 / 1`。68 项执行错误中，67 项的错误链含 `_ctypes`，1 项是 `_ssl`。5 条断言失败发生在迁移测试，它们原本应看到注入的迁移故障或预期元数据，实际先收到 `Migration barrier unavailable; retry after active transactions/migration finish` 一类结果。分类脚本的退出码为 0，它只打印分类，没有把失败计数转换成退出状态。判断复现结果依据测试框架计数。

| 触发点 | 源码与因果关系 |
|---|---|
| 导入 `ctypes` | [迁移屏障](../../workbench/execution/migration_barrier.py) 在 Windows 获取锁或构建屏障时依赖 `ctypes`。初始化失败可以同时产生执行错误，以及因未到达注入位置而报告的断言失败 |
| 导入 `ssl` | [能力探测](../../workbench/agents/capability_probe.py) 在模块导入阶段加载 `ssl`。这一异常发生在请求上游服务之前 |
| 受 Windows 模块导入影响的测试 | [托管进程](../../workbench/execution/managed_process.py) 等模块使用 `ctypes`。模块级导入失败后，`unittest` 会生成导入失败的占位项，模块内的真实用例无法完整展开 |
| Git 结果 | 本次的提交和推送另外取得成功。单测失败计数和 Git 命令结果要各自看退出码与运行记录 |

19:38 在同一个工作台直接启用基础 Python，用相同发现范围重新分类，结果为 `201 项，0 失败，0 错误，1 跳过`，实际通过 200 项。唯一跳过的是要求 `RUN_LIVE_AGENT_TESTS=1` 的真实 Codex CLI 语义计划集成测试。`81` 和 `201` 来自不同的发现结果，前者出现导入占位以及一批无法展开的用例。`201` 项基线使后续可以分开追踪加载环境和测试行为。

### 2.2 为什么回看主分支和更早的工作台

用户指出当天主要调整了 Git 和文档编辑权限，希望确认时间相关性。历史记录如下。

| 时间 | 启动和观察 | 意义 |
|---|---|---|
| 2026-10-20 19:26:19、20:08:03 | 旧工作台在 `workspace-write` 下两次记录 `_ssl` 导入初始化失败，当时未使用命名权限配置 | 起初即有相同扩展的错误。缺少当时的初始化断点，无法追加追溯到后来定位的 User32 机制 |
| 2026-11-15 15:57:54 | 旧工作台使用 `workspace-write`，相关隔离运行中已观察到 `_ssl`、`pywintypes` 的 DLL 导入问题，影响 Web 宿主导入。相关限制记录在 [README 设计](../design/PROJECT_README_DESIGN.md) | 提供更早的同类 Windows DLL 初始化线索 |
| 2026-11-20 01:28:07 | 当时工作台使用 `workspace-write`，尚未使用当前分支的命名 `step1-git` 路径。运行包含 `test_capabilities` 的选定单测，42 项、25 项执行错误、1 项跳过，已经同时记录 `_ssl`、`_ctypes` 失败 | 相同症状早于当前 Git 权限改动 |
| 2026-11-21 主分支基线 | [主分支源码目录设计](../design/SOURCE_LAYOUT_DESIGN.md)已记录两类导入错误，相关单测当时通过直接调用基础 Python 执行 | 此问题具有可回溯的历史，基础解释器对照也有先例 |
| 2026-11-21 当前分支 | 对照 `c8f2e0a..88b3b73`，相差 1 个提交，18 文件、841 行新增、34 行删除。其中 [Codex 启动适配](../../workbench/adapters/web_backends.py)给本机匹配工作目录的普通任务添加命名权限分支，续接把选项放在 `resume` 前。Grok 文件工具和进程诊断也有调整 | 当天的变更决定启动参数或增加观察点。基础解释器、虚拟环境、依赖清单、迁移屏障和托管进程实现没有在这个差异中变更 |

普通任务同一套选择逻辑服务于新建和续接。工作台原有的只读汇总、其他目录的 `workspace-write` 默认路径及宿主子进程环境复制，是比对时考虑的启动边界。[Git 权限设计与验收记录](CODEX_GIT_PROFILE.md)

Grok 的 `workbench_files` 是另一条执行路径。适配在宿主自己的解释器中启动相应工具，Windows 上执行本地操作时可能用到 `ctypes`。`.venv` 独立导入的复现命令没有加载项目文件工具，且更早的工作台已有故障。已有证据不足以把当日的文件工具调整或 `.git` ACL 变化归为此次 DLL 根因。

## 3. 首轮隔离与基础解释器对照

### 3.1 解释器、二进制、错误字符串

- `.venv\pyvenv.cfg` 的 `home` 与 `executable` 指向同一个基础 CPython 3.13.7 x64，`include-system-site-packages=false`。Windows 的虚拟环境启动器会派生基础解释器，运行中的 `sys.executable` 可继续显示虚拟环境路径，`sys._base_executable` 则指向基础解释器。两种调用实际使用同一套基础运行时。
- 核对了 `python313.dll`、`vcruntime140.dll`、`_ctypes.pyd`、`libffi-8.dll`、`_ssl.pyd`、`libssl-3.dll`、`libcrypto-3.dll` 等文件的存在和 x64 架构。静态 ACL 检查确认普通用户的基础访问，沙箱令牌的有效访问另外从现场捕获核实。
- 使用 `-X utf8` 的隔离探针拿到完整错误文本 `DLL load failed while importing _ctypes/_ssl: 动态链接库(DLL)初始化例程失败。`，对应 Win32 `ERROR_DLL_INIT_FAILED`（1114）。早期截断和乱码输出仅保留各自可读证据。
- 19:45:36，本次排查终端中，原 `.venv` 与基础 Python 各自的两项导入均退出 0。DeepSeek Harness 中也能正常导入两个模块，去掉 PATH 中的基础 Python 安装路径后仍成功。工作台失败路径中显式调用 `os.add_dll_directory` 的 `_ctypes` 探针仍失败。结合后续映射记录，系统 PATH 缺项不足以解释现象。
- 另有对照发现失败运行中的 `_hashlib` 也可能加载失败，同时 `_sqlite3`、`_socket`、`_bz2` 可导入。`hashlib` 的部分功能可使用回退实现，不能从 `_hashlib` 单项结果推导所有散列都失败。按绝对路径单独加载探针曾观察到 `libcrypto-3.dll` 初始化失败、`libffi-8.dll` 可以加载而 `_ctypes` 仍失败。单独探针与扩展完整初始化的现场各有用途。

### 3.2 四组最小导入探针

固定同一工作台、工作目录及执行权限，分别运行 `.venv` 和基础解释器，给 `_ctypes`、`_ssl` 每个模块独立进程，避免第一个导入异常阻断另一个。输出 PID、父 PID、`sys.executable`、`sys._base_executable` 以及成功导入的模块文件路径。各轮用不同标记关联调用记录与 ProcMon。下面为同等操作示例，先替换两个路径占位符。

```powershell
$project = '<PROJECT_ROOT>'
$basePython = '<BASE_PYTHON_EXE>'
$venvPython = Join-Path $project '.venv\Scripts\python.exe'
$probe = 'import os, sys, importlib; print(os.getpid(), os.getppid(), sys.executable, sys._base_executable); print(importlib.import_module(sys.argv[1]).__file__)'

Push-Location $project
try {
    foreach ($python in @($venvPython, $basePython)) {
        foreach ($module in @('_ctypes', '_ssl')) {
            & $python -I -B -u -X utf8 -c $probe $module DLL_IMPORT_PROBE
            $exitCode = $LASTEXITCODE
            Write-Output "$module exit=$exitCode"
        }
    }
} finally {
    Pop-Location
}
```

`-I` 降低用户环境变量和用户模块路径干扰，`-B` 避免字节码写入，`-u` 便于按顺序留存输出，`-X utf8` 避免中文诊断再次丢失。第一次 20:00 的探针因为 PowerShell 对 `-c` 内嵌引号转义，将 `print("pid=", ...)` 实际传成无效表达式，四组在导入之前都报 `SyntaxError`。上例只使用以变量传入的无内嵌字符串字面量表达式，并从 `sys.argv` 取模块名。在同一个会话重新执行的四组中，虚拟环境两个退出 `1`，基础解释器两个退出 `0`。

### 3.3 去掉 site、只传虚拟环境元数据、另外建一套 .venv

| 对照 | 控制方法 | 结果和能回答的问题 |
|---|---|---|
| 去掉 site，20:15:55.788–20:15:56.051 | 四组使用 `-I -S -B -u -X utf8`。`-S` 禁止正常 `site` 初始化，CPython 3.13 的这一组合下 `sys.prefix` 可回到基础环境 | 原 `.venv` 两项仍退出 1，基础解释器两项退出 0。`site`、`.pth` 或常规站点包初始化不是复现的必要条件 |
| 直启基础解释器并传虚拟环境元数据，20:18:06 | 仅在探针进程作用域设置 `__PYVENV_LAUNCHER__` 为原虚拟环境的解释器路径，然后直接启动基础 Python。按同组参数测试后恢复变量 | 两项退出 0。即使 `sys.executable` 显示为虚拟环境路径，单靠这项元数据仍不会触发故障 |
| 普通 Python 父进程另起基础 Python | 保留同等导入参数，另用普通 Python 子进程方式启动基础 Python，测试包含 `close_fds=False` 的对照 | 两项退出 0。一般的多层进程关系不足以单独复现 |
| 独立临时虚拟环境，20:21:16 创建、20:22:12 返回工作台结果 | 同一基础解释器执行 `-m venv --without-pip`，比较新旧两个 `Scripts\python.exe` 的 SHA-256 | 新旧均为 `200D001707ED494066EF00E31E38330E32490C2A74A450CA5D66D2179396FD0E`。两套虚拟环境分别在同一工作台 Codex 路径都失败，基础解释器通过。20:22:41 临时环境在本次排查终端下两项通过 |
| 直启基础解释器并保留 site | 临时设置相同启动元数据，不带 `-S`。在工作台对应探针中导入 `ctypes`、`ssl`、`mcp.server.fastmcp.FastMCP` | `IMPORT_PROBE_EXIT=0`，同时能取到项目环境的 MCP 包。该路径属于诊断对照，未作为生产配置部署 |

独立临时虚拟环境实验只创建、清理了专用测试环境，项目原 `.venv` 保持原状。给同一启动器重新复制环境文件仍会走同一条有问题的启动路径，因此没有重建正式 `.venv`。`__PYVENV_LAUNCHER__` 属于 CPython 内部启动约定。子进程若再次以 `sys.executable` 派生 Python 仍可能经过原启动器，所以这个探针也不足以说明整个工作台采用混合启动方案后可稳定运行。

从 CPython 3.13.7 源码看，Windows 虚拟环境启动器通过 `CreateProcessW` 等 API 启动基础解释器，并使用带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`、`JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` 的启动器 Job。启动器本身及其派生解释器是否仍在工作台的上层 Job，后面用独立的 Job 成员测试核实。[Python venv](https://docs.python.org/3.13/library/venv.html) · [Python 命令行 -S](https://docs.python.org/3.13/using/cmdline.html#cmdoption-S) · [CPython 3.13.7 Windows 启动器](https://github.com/python/cpython/blob/v3.13.7/PC/venvlauncher.c) · [CPython 3.13.7 路径初始化](https://github.com/python/cpython/blob/v3.13.7/Modules/getpath.py)

## 4. 用 ProcMon 区分文件访问、映射和初始化

### 4.1 捕获过程

1. 在 Process Monitor 控制台确认 `File → Capture Events` 开启。先用 PID 筛选，尝试保留两种 Python 启动路径的目标事件。20:01:31.970–20:01:32.293 的有效探针拿到两组 `.venv` 启动器与子解释器 PID（`19428 → 6504`、`68604 → 63740`），基础解释器是 `57980`、`41636`，父 PowerShell 均为 `44776`。但当时已积累数百万事件的 ProcMon 窗口在重置和重新筛选后仍一片空白。`Filter → Drop Filtered Events` 当时未勾选。仅看到过滤后的显示结果，无法确认空白是何种具体操作或软件状态所致，也无法用这轮空白支持任何文件权限结论。
2. 用户重启 ProcMon 后确认列表实际出现事件。新捕获使用 `Process Name is python.exe → Include`，可以同时纳入 `.venv` 启动器和真实 Python 子进程。控制台运行四组最小探针后立即停止捕获，选择当前筛选显示的事件并导出为 CSV。后处理按 PID、顺序、操作和相对安装路径筛出关联事件。
3. 20:09:57 这轮有效捕获导出约 7.58 MB。CSV 有 37,715 个物理数据行。部分字段跨行，按带引号和换行字段的 CSV 格式解析后得到 37,246 条事件。父 PowerShell PID 为 `57136`。虚拟环境 `_ctypes` 由 `10112` 启动 `35536`，`_ssl` 由 `46156` 启动 `46712`。基础 Python 直接运行的两个 PID 分别为 `7040` 和 `65884`。分析扩展 DLL 时按实际解释器 PID 取证。[Microsoft Process Monitor](https://learn.microsoft.com/sysinternals/downloads/procmon)

### 4.2 实际文件事件

下表时间均是 `20:09:57` 后的小数秒。两组 `.venv` 的真实解释器退出 1，两组 direct base 退出 0。安装路径写成相对基础 Python 安装目录的路径。

| 启动和导入对象 | 进程 | 扩展与主要依赖 | 进程退出 |
|---|---:|---|---|
| `.venv` → `_ctypes` | 35536 | `DLLs\_ctypes.pyd` `CreateFile SUCCESS` .1283701、`Load Image SUCCESS` .1285102。`DLLs\libffi-8.dll` 打开 .1294658，映射 .1296021 | .1963418，退出 1 |
| `.venv` → `_ssl` | 46712 | `DLLs\_ssl.pyd` 打开 .2712263、映射 .2713512。`libcrypto-3.dll` 和 `libssl-3.dll` 打开 .2728421、.2728719，映射 .2729598、.2730037 | .3266722，退出 1 |
| 基础 Python → `_ctypes` | 7040 | `_ctypes.pyd`、`libffi-8.dll` 从相同路径打开 .3810182、.3822292，映射 .3811860、.3823875 | .3964534，退出 0 |
| 基础 Python → `_ssl` | 65884 | `_ssl.pyd` 打开 .4526121、映射 .4527807，两个 OpenSSL DLL 从相同路径打开 .4543327、.4543514，映射 .4544904、.4545409 | .5055816，退出 0 |

- 两条 `Process Create SUCCESS` 事件直接记录了虚拟环境启动器派生基础 Python。`20:09:57.0828272`，PID `10112` 创建子 PID `35536`。`20:09:57.2273605`，PID `46156` 创建子 PID `46712`。两个事件的目标都是创建原 `.venv` 的同一个基础 `python.exe`。
- `_ctypes` 的两组各有 29 个映射成功事件，去重后是相同的 28 个映像路径，其中 `python3.dll` 各出现两次。`vcruntime140.dll` 来自同一个基础安装路径。`_ssl` 的 `.venv` 组为 29 个成功事件、28 个唯一路径，基础组为 30 个成功事件、29 个唯一路径。虚拟环境组独有 `msasn1.dll`，成功的基础组独有 `_socket.pyd`、`IPHLPAPI.dll`。映像差异可以反映进度和所走分支，仍需初始化现场才能判断因果。
- 四组捕获没有看到会解释失败的主要扩展／依赖 `CreateFile ACCESS DENIED`、分享冲突或坏映像。唯一观测到的 `ACCESS DENIED` 位于成功的基础 `_ssl` 组。`20:09:57.4942849`，`RegOpenKey` 请求 `HKLM\System\CurrentControlSet\Services\WinSock2\Parameters` 的 All Access 被拒，随后导入仍成功并退出 0。
- 路径搜索中出现两类结果。四组对 `Modules\Setup.local` 的 `QueryOpen` 均为 `PATH NOT FOUND`，分别发生于该秒的 .1019916、.2467271、.3558965、.4271330。`pyvenv.cfg` 的部分候选位置为 `NAME NOT FOUND`，实际配置位置有成功事件。两类探测都需要与真实模块、配置路径的成功访问一起解读。
- `Load Image SUCCESS` 表明系统找到了文件并映射映像。随后 Windows Loader 仍需执行依赖的进程附加初始化。初始化代码可能返回失败且不在 ProcMon 中表现为该 DLL 文件打开失败，因此下一步转向调试器。[Microsoft DllMain](https://learn.microsoft.com/en-us/windows/win32/dlls/dllmain)

## 5. 用 WinDbg 定位 DLL 初始化失败

### 5.1 为什么附加到原启动路径

用户安装 WinDbg 后，取证使用其 x64 CDB，版本 `10.0.29680.1004`，WinDbg 应用包版本 `1.2611.4001.0`。从当前受限终端访问 WindowsApps 的包信息及启动调试器曾被拒绝，之后从普通 Windows 诊断进程确认 CDB 可用。这个诊断访问限制与被测 Python 的 DLL 初始化失败分开处理。

为了保留现场，让工作台 Codex 先通过原 `.venv` 启动真实 Python，探针写出可核对的进程标识，并有时限地等待继续信号。核对 PID、父 PID、解释器路径和本次唯一探针标记，再把 CDB 附加到这一进程。闸门放行后由同一 Python 导入模块。先在调试器里启动另一个 Python 会改变父进程和继承上下文，无法回答此前的 A/B 问题。调试只操作为本轮创建的进程和等待信号。Loader Snaps 使用 `!gflag +sls` 针对已核对的进程短时启用，无持久化的 GFlags 或 IFEO 修改。

### 5.2 调试过程中的无效尝试

| 尝试 | 实际发现 | 后续处理 |
|---|---|---|
| 第一轮附加 `_ssl` 进程，真实 Python PID `24016`，启动器 `42108` | 在进程内启用 Loader Snaps 后可读到 `NtGlobalFlag=2`，能复现导入错误并看到 `ModLoad`，但符号加载和诊断命令相互影响，未读到准确的初始化返回点 | 保留为复现和装载证据。接下来单独解析 Loader 汇编后设断点 |
| 查 `USER32` 初始化函数符号 | 公开符号能给出名称，出现重复或含糊的 `UserClientDllInitialize` 指向 | 先确认模块及其相对地址，在模块实际加载后解析 |
| MASM 表达式和挂起断点 | 条件表达式写法 `&&` 产生数值解析错误，模块尚未加载时的 `bu USER32+…` 未正确解析 | 将断点条件和动作分开，在实际加载时再下断点。这些失败没有提供被测代码的执行结论 |
| 从嵌套事件回调运行 `$$<` | 脚本查找报 Win32 错误 2 | 让 CDB 从干净的标准输入逐条执行。用 `sxe ld:USER32.dll`、`g` 等待模块加载，再设置断点并继续 |
| 停滞的诊断进程 | 某些早期 CDB 或等待闸门的探针未自然完成 | 验证归属后清理本轮创建的探针或调试进程，后续采用新进程复现 |

### 5.3 有效断点与读数

先反汇编当前 Windows 的 `ntdll!LdrpCallInitRoutine`。这个构建中，`LdrpCallInitRoutine+0x93` 是内部调用 `LdrpCallInitRoutineInternal` 刚返回后的位置。检查这一指令时，`AL` 是 DLL 初始化例程的布尔返回低位，`R14D=1` 对应 `DLL_PROCESS_ATTACH`，`R15` 是模块映像基址。根据模块基址和映像区间识别 DLL，用 `!gle` 查看线程最后 Win32 错误和 NT 状态。这些寄存器分配及 `+0x93` 偏移来自当前二进制的反汇编。其他 Windows 构建应重新解析对应调用点和寄存器，不能照抄偏移。

本次调试器中实际用于 `_ssl` 的断点如下，第一层只检查进程附加，第二层只对返回 0 抓取完整模块信息、线程状态和调用栈。`gc` 让诊断断点继续执行。

```text
u ntdll!LdrpCallInitRoutine+0x93 L1
bp ntdll!LdrpCallInitRoutine+0x93 ".if (@r14d == 1) { .echo COAGENT_ATTACH_RESULT; ? @al; lm a @r15; .if (@al == 0) { .echo COAGENT_ATTACH_FAILURE; lmv a @r15; !gle; kb 10 } }; gc"
```

第二次在 `_ctypes` 探针中，用 `dx -r1 ((ntdll!_PEB*)@$peb)->ProcessParameters->DesktopInfo` 读桌面元数据。等待 `USER32.dll` 加载后，才在按本机反汇编选定的四个相对地址下断点。命中 `USER32+0x6abb5` 的 CSR 连接返回点和 `USER32+0x6b2b5` 的失败路径。两个选定的 `NtUser` 返回候选点 `+0x4d927`、`+0x4da2f` 没有命中预设标记。

| 探针 | 现场 | 结果 |
|---|---|---|
| `_ssl`，真实 Python PID `18940`，启动器 `63600` | 原工作台的 `.venv` 启动子进程，断点落在 `USER32.dll` 初始化返回 | System32 的 `USER32.dll` 版本 `10.0.26100.9549`，`DLL_PROCESS_ATTACH` 返回 `AL=0`。`!gle` 同时报告 LastError `0x00000006`、LastStatus `0xc0000022`。Python 最终报 DLL 初始化失败 |
| `_ctypes`，真实 Python PID `1304`，启动器 `12400` | 继续检查 PEB 的 `DesktopInfo` 和 User32 相关分支 | `DesktopInfo` 显示 `Winsta0\CodexSandboxDesktop-<suffix>`，进程在 Session 1。`CsrClientConnectToServer` 返回 0，之后记录到 `USER32.dll` 初始化失败分支及相同的线程状态 |
| 继续细化 `_ctypes` 拒绝点 | 探测若干 `NtUser*` 候选返回点 | 探测未命中预设返回标记。只能确认所在初始化链及当时线程状态，不能从 `!gle` 单独认定拒绝访问来自某个 API、私有桌面对象或特定 ACL |

`_ssl` 抓到的栈包含 `python313!_PyImport_FindSharedFuncptrWindows → KERNELBASE!LoadLibraryExW → ntdll!LdrLoadDll → LdrpInitializeGraphRecurse → LdrpInitializeNode → LdrpCallInitRoutine`，确认它发生在 Python 导入扩展的依赖初始化链。`_ctypes` 第一次命中内部失败分支后，外层 Loader 返回点再次确认同一个 `USER32.dll` 的失败。这两段分别完成于约 20:46 和 20:53。

`0xc0000022` 对应 `STATUS_ACCESS_DENIED`。`0x6` 对应 `ERROR_INVALID_HANDLE`。Windows Loader 对附加初始化失败的外层报错是 `ERROR_DLL_INIT_FAILED`，这与 Python 导入时看到的 1114 一致。这几种代码出现在不同层次。后续单测中的 `0xc0000008` 是另一场景的无效句柄状态，分别记录。[Microsoft NTSTATUS](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/596a1078-e883-4972-9bbc-49e60bebca55) · [进程和窗口站的连接方式](https://learn.microsoft.com/en-us/windows/win32/winstation/process-connection-to-a-window-station)

机制判断的置信边界：

- 已实测同一基础运行时和 DLL 文件，失败由原工作台中经 Windows 虚拟环境启动器进入的运行上下文复现。ProcMon 排除了观测到的主要文件访问路径差异，WinDbg 确认 User32 初始化确实失败。
- 私有桌面及启动器派生上下文与现象吻合，属于最有证据支持的方向，后面使用 MXC 做进一步隔离。仍没有逐一改变原沙箱的桌面对象、令牌和 Job 三个变量来单独归因，也没有对未知 ACL 做放宽实验。
- 现场进程在 Session 1。其他 Windows 报告中发生在 Session 0 的桌面初始化现象可提供排查思路，不能替代本机证据。没有依赖修改 Codex 二进制、注册表全局 Loader 设置或已移除的私有桌面实验参数。

### 5.4 功能影响和判断方法

| 路径 | 有依据的影响 |
|---|---|
| 项目 Python 测试 | 已确认由独立导入扩展失败引起模块发现、Windows 托管进程及迁移屏障测试异常，级联出多条执行错误和断言失败 |
| 用同样失败进程运行 Python 或 Web 宿主 | 依赖 `ctypes` 的 Windows Job/文件锁，以及使用 `ssl` 的 CLI/网络功能，可能在加载时就无法运行。`ssl` 异常可发生在任何 TLS 握手之前 |
| 本次实际运行的网页 | 当前网页使用基础 Python 启动，配置切换前能访问并返回 HTTP 200。故障集中发生在网页转发给 Codex 后，命令从 Codex 沙箱内经 `.venv` 二次启动时 |
| 直接 `git.exe`、其他 Agent 和未走该 Python 路径的功能 | 本次 Git 提交推送先后独立取得成功。其他 Agent 的运行上下文独立，DeepSeek 的同项导入通过。依赖 Python 的 Git 钩子等仍需按实际进程的启动路径核验 |

## 6. 为什么选择 MXC，以及最初的验证

### 6.1 处理方案选择

原全局 Windows 配置为 `windows.sandbox=elevated`，`prefer_mxc` 未设置。诊断目标是保留项目原 `.venv`、项目读写策略和工作台托管能力，改变承载这些命令的 Windows 沙箱后端。项目本机 Windows 11 25H2 `26200.9711` 属于官方文档所说明的支持范围。可用性仍以当前 CLI 和当前启动链实际启用结果为准。[Codex Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)

| 备选 | 依据和取舍 |
|---|---|
| 用同一个基础解释器重建原 `.venv` | 已通过独立同版本临时虚拟环境重复得到相同错误。重建会扰动项目依赖，不能改变同一启动器走的运行路径 |
| 将工作台测试统一改成基础 Python | 直接基础 Python 是有价值的对照，也能用于启动外层验收脚本。它缺省没有项目安装于 `.venv` 的依赖。把生产调用一律换成基础解释器会改变运行环境 |
| 给直接启动的基础解释器注入虚拟环境元数据 | 受控导入通过，也能发现 MCP 包。它依赖 CPython 内部变量，并未覆盖递归派生、安装命令和工作台管理的全部路径 |
| 调整 Windows 全局 ACL、关闭隔离或改写用户全局 Codex 配置 | 现场尚未定位到确切 ACL，且同样的前期排查目标可以用项目级后端验证，未采用这些方案 |
| 本项目显式使用 MXC | 可独立设置 Windows 后端，同时继续使用原命名权限配置与项目 `.venv`。先带一次性命令参数验证，确认兼容边界后才落盘 |

### 6.2 一次性运行先验收导入、权限与启动

先从普通 Windows 终端直接启动 Codex，用命令行 `-c windows.sandbox=mxc` 只影响本次实验。用 `--include-managed-config` 和已配置的 `--permission-profile step1-git`，保留同一项目持有的文件与网络策略。先执行输出 `MXC_OK` 的最小命令，再用下面这种调用在原虚拟环境中导入真实模块。

```powershell
$project = '<PROJECT_ROOT>'
$codex = '<CODEX_CLI_EXE>'
$venvPython = Join-Path $project '.venv\Scripts\python.exe'
$probe = 'import sys, ctypes, ssl, mcp; print(sys.executable, sys.prefix, ssl.OPENSSL_VERSION, mcp.__file__)'

& $codex -c 'windows.sandbox=mxc' sandbox --include-managed-config `
    --permission-profile step1-git --cd $project -- `
    $venvPython -I -B -u -X utf8 -c $probe
$LASTEXITCODE
```

最小命令和导入命令都退出 0，`sys.prefix` 指向项目 `.venv`，MCP 来自其中的 `Lib\site-packages`。用 `git --no-optional-locks status` 检查 Git 读路径也退出 0。另用 `CreateFileW(FILE_ADD_FILE, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS)` 对 `.git` 检查创建子项所需的目录访问，并关闭句柄，成功取得访问。该句柄探针没有创建文件，同时这一轮没有做 MXC 下的真实提交、分支变更或远端推送。

这一步回答了新后端能否启用、能否运行原解释器，以及现有权限配置是否至少通过已测的访问检查。后续还需单独验证完整单测、取消进程及实际工作台会话。

### 6.3 完整单测先发现临时目录问题，再得到五项可分析问题

| 时间 | 运行条件 | 实测结果 | 由此采取的动作 |
|---|---|---|---|
| 21:01:08 发起，21:03:21 返回 | MXC，原始单测，使用该后端默认的 AppContainer `AC\Temp` | 201 项、69.282 秒，14 failures、300 errors、1 skipped。看到 SQLite `unable to open database file`，清理临时目录 `WinError 5` | 先解决测试运行时的临时目录位置。单项测试的运行与清理可以分别报错，执行错误数可大于总用例数 |
| 21:03:50 发起，21:04:37 返回 | 保持 MXC 和同一项目解释器，将临时目录指定到项目内。进程启动后设置 `TEMP`、`TMP`、`TMPDIR` 及 Python 已装入模块的 `tempfile.tempdir` | 201 项、32.826 秒，3 failures、2 errors、1 skipped，退出 1 | 数据库和大范围临时目录连锁错误消失。对剩余五项逐项证明失败发生在哪一步，避免把环境问题和真实代码问题合并 |

剩余五项的原始结果：

| 分类 | 用例 | 实际失败位置和最初能证明的事实 |
|---|---|---|
| 断言失败 | `test_breakaway_is_rejected` | 实际 `(6, b'')`，用例期待 `(0, b'blocked')`。6 是测试脚本在 `Popen` 返回后主动退出的哨兵码，说明创建调用没有抛出拒绝。原测试在这一条断言处结束，没有验证进程实际离开 Job |
| 断言失败 | `test_service_and_migration_lock_handles_excluded` | 子进程调用 `GetFinalPathNameByHandleW` 得到 `OSError [WinError -1073741816]`，即 `0xc0000008`，进程退出 1，外层断言原先要求退出 0。两个句柄的检查未完整执行，不能据此认定锁泄漏 |
| 断言失败 | `test_junction_alias` | `mklink /J` 退出 1，外层创建断言失败，尚未执行重复实例锁断言 |
| 执行错误 | `test_alias_and_hardlink` | 路径大小写、点别名的检查已通过，随后 `os.link` 触发 `WinError 5`。新建 hardlink 未完成，尚未调用实例保护逻辑 |
| 执行错误 | `test_hard_link_is_rejected` | `os.link` 被拒绝，尚未调用 edit/delete 的拒绝逻辑 |

以上三个夹具失败均发生在被测行为之前，后续将准备夹具和执行测试分开。另两项需查实际 Job、句柄状态，避免将较窄的预期返回值当作产品的正确性条件。

## 7. 修改测试前补做的 Job 与句柄验证

### 7.1 breakaway 实际能否离开工作台的 Job

改变测试前，保持生产的 [ManagedProcess](../../workbench/execution/managed_process.py) 不动，在普通宿主和 MXC 中做相同的对照，结果于 21:20:07 返回。根探针申请 `CREATE_BREAKAWAY_FROM_JOB`。如果创建成功，分别取得 `Popen` 返回的 launcher PID 和实际执行 Python 脚本的子解释器 PID，直接调用 `IsProcessInJob(handle, owner.job_handle, …)`。随后由 `ManagedProcess.cancel()` 取消并用进程句柄验证是否已退出。这里传的是 CoAgent 实例自己的 Job 句柄，能够回答是不是属于对应 Job。

| 环境 | 被创建的解释器 | 创建与验证结果 |
|---|---|---|
| 普通 Windows 宿主 | 项目 `.venv` | `Popen` 成功，launcher PID `33340`、解释器 PID `42500`。两者对 CoAgent Job 查询均为 true。取消状态 `cancelled`，退出已确认，两句柄零超时等待均返回 `0` |
| MXC | 原项目 `.venv` | `Popen` 成功，launcher PID `22428`、解释器 PID `47336`。两者均在 CoAgent Job，取消后两句柄同样返回已退出 |
| 普通宿主及 MXC | 直接基础解释器 | 两条路径都返回 `blocked:5`，根探针正常完成并确认退出 |

两种环境都允许观察到某些启动路径创建成功，但 CoAgent 的 Job 仍能包含和取消受控进程。Windows 支持嵌套 Job，子进程退出内层启动器 Job 不必退出上层 Job。原测试强制所有路径固定抛出错误 5，过早结束验收。新测试保留对拒绝路径的严格检查，并在创建路径实现控制对象的验证。这个矩阵证明的是本次测试的进程树和 Job，不能泛化为对任意第三方程序的完整系统权限证明。[Microsoft 嵌套 Job 对象](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)

### 7.2 为什么识别无效句柄及核对真实路径

Windows 传给子进程的句柄数字只是该进程的句柄表索引。父进程的锁句柄写为 inheritable，故意用于验证进程创建时限制继承句柄的正确性。子进程里的相同数值有两种符合预期的结果：

- 新进程尚未将这一数值分配给有效资源。常规实现报告 `ERROR_INVALID_HANDLE=6`，本轮 MXC 的文件路径 API 还观察到有符号值 `-1073741816`，按 32 位解释即 `STATUS_INVALID_HANDLE=0xC0000008`。两者都可作为明确的无效句柄结果。
- 新进程已对另一个资源复用这一数值。检查必须读取有效句柄对应的真实路径，避免把数值相等当作原锁对象被继承。若真实路径等于服务锁或迁移锁任一个，测试失败。

如果探针返回其他错误、结果缺失、路径缓冲区不完整，依然无法证明隔离，应报错。测试保留对两个锁描述符逐一核验及退出确认。

## 8. 测试改了什么、每项为什么改

这轮变更以刚才五项的实际失败点为依据。共修改三个现有单测文件，新增一份夹具定位模块和一个 MXC 验证入口。测试仍调用项目实际的实例锁、文件操作和 ManagedProcess。整个故障处理没有改生产模块。

### 8.1 tests/unit/test_managed_process.py

[托管进程单测](../../tests/unit/test_managed_process.py)

| 改动 | 原因及验收 |
|---|---|
| `hold_process(self, pid, access=0x100000)` 允许显式传访问类型。其他测试沿用等待权限，新测试传 `0x101000` | 除 `SYNCHRONIZE` 外，本测试还调用 `IsProcessInJob`，需要查询权限。把新增访问限定在需要的两个检查 |
| `test_breakaway_is_rejected` 改为 `test_breakaway_cannot_escape_managed_job` | 名称跟随实际受测属性。进程是否逃离本项目的 Job 才是要保护的行为 |
| 分别记录 `Popen` 请求结果和真实解释器 PID，写完整行，通过 `wait_for` 有界等待并在读取时验证记录。测试进程最多自行等待 12 秒，返回 PID 相同则去重 | `.venv` 启动器和运行脚本的解释器可能不同，必须检查两层。等待完整内容可避免刚创建的空标记被当作有效 PID |
| 创建被拒时限于错误 5，要求根运行 `completed`、退出码 0、退出确认，而且真实解释器未写启动标记 | 保留受限创建路径的原验收强度，其他异常继续出错 |
| 创建成功时为对应 PID 取得进程句柄，对每个子进程查询 `IsProcessInJob(handle, owner.job_handle, …)`，取消后逐个调用 `WaitForSingleObject(handle, 0)`，并检查取消及退出确认 | 托管进程实际脱离控制、无法终止或状态误报都会失败。观察具体 Job，比只看 `Popen` 是否抛错更能发现回归 |
| 锁句柄探针为每个输入按原顺序输出一条结构化 JSON。检查输入和输出对应、两条都存在，同时确认进程正常退出 | 防止第一条探测异常让第二条悄悄未被验证。任一缺失均为测试失败 |
| 对 `GetFinalPathNameByHandleW` 的 0 返回值读取错误，按 `& 0xffffffff` 将 Win32 及带符号 NTSTATUS 值归一。只允许已证实的 6 及 `0xc0000008` 作为无效句柄。通用异常和未读全的路径继续失败 | 修正 MXC 的错误表示差异，保留对执行异常与真实泄漏的检测 |
| 对有效句柄路径处理 `\\?\`、`\\?\UNC\`，再标准化大小写和路径形式，并与服务锁、迁移锁两条完整路径比较 | 路径别名不产生误判，句柄数值在子进程复用时仍能判断真实对象 |
| 去掉这个用例不被断言使用的 `observed/on_created` 闭包，维持 `finally` 中恢复原锁描述符状态 | 读取的 Windows 句柄和结构化结果已提供有效证据。保留探针结束后还原句柄状态，避免污染后续用例 |

独立的未列入继承名单的命名事件句柄测试继续运行，测试覆盖面包含文件锁和一般句柄。

### 8.2 tests/unit/test_service_instance.py

[服务实例单测](../../tests/unit/test_service_instance.py)

| 改动 | 原因及验收 |
|---|---|
| 把 `test_alias_and_hardlink` 拆成 `test_path_alias` 和 `test_hardlink_rejected` | 普通路径别名与硬链接分别报告。夹具创建异常时也能保留普通路径规则的独立结果，真实单测数量由 201 变为 202 |
| junction 用例先获取可选宿主 fixture，无时仍在普通运行创建。验证 `IO_REPARSE_TAG_MOUNT_POINT`、两目录及两个 DB 路径各自 `samefile`、数据库 `st_nlink == 1` | 证明两个调用名经过真实 junction 指向同一个 DB。排除错误夹具碰巧触发别的拒绝 |
| 通过原路径持有实例保护，再用 junction 别名请求同一 DB，要求 `RuntimeError` 含 `execution-service lock`。读取数据库字节确认未变化 | 验收真正的重复实例拒绝和数据完整性。本地建的 junction 在 `finally` 中以 `os.rmdir` 移除，宿主预建的由宿主回收 |
| hardlink 用例确认原名、别名是同一文件，两者链接数都是 2。分别用两个路径调用保护逻辑，要求错误含 `hard link`，核对 DB 内容未变化 | 任一路径都可能被误放行，分别检查比只检查别名完整。内容校验确保拒绝前没有先写入数据 |

### 8.3 tests/unit/test_workspace_files.py

[工作区文件单测](../../tests/unit/test_workspace_files.py)

| 改动 | 原因及验收 |
|---|---|
| `test_hard_link_is_rejected` 使用可选宿主 fixture 或原有的 `os.link` 自建路径。同文件及 `st_nlink == 2` 是前置断言 | MXC 中仅将文件对象的生成移出执行环境。场景依旧由真实 NTFS hardlink 触发 |
| 分别调用实际 `operate` 的 edit 和 delete，要求 `ValueError` 含 `one link`。每次操作后核对原始文件内容、路径 `samefile` 及链接数 | 同时验证两种写操作的拒绝理由，且拒绝后原件和别名都完整。夹具异常或行为错误仍以测试失败呈现 |

### 8.4 tests/fixtures/link_fixtures.py

[真实链接夹具定位](../../tests/fixtures/link_fixtures.py)

- 新增 `prepared_link_fixture(name)`，从测试专用环境变量 `COAGENT_LINK_FIXTURES` 获取宿主预建根目录。变量未设置时返回无夹具，原正常宿主路径仍直接调用 OS 创建测试对象。
- 变量有值时要求绝对路径，并确认指定夹具是存在的目录。路径或对象缺失直接失败，避免错误配置绕回自建、使用无关工作目录或悄悄丢掉覆盖。
- 在宿主运行器创建 `instance-hardlink/{isolated.db,linked.db}`、`instance-junction/{real/isolated.db,alias}` 和 `workspace-hardlink/{note.txt,sub/alias.txt}`。前两组用于实例锁，第三组用于文件编辑和删除。其是否真为指定链接由单测检查；宿主负责其生命周期。这是测试入口的夹具约定。

### 8.5 tests/verify/run_mxc_unit.py

[MXC 单测验证入口](../../tests/verify/run_mxc_unit.py)

| 改动 | 原因及验收 |
|---|---|
| 明确在普通 PowerShell 里用已安装的基础 Python 直接运行外层脚本。内层 `--python` 默认为项目 `.venv\Scripts\python.exe`，并在启动前解析存在性 | 用外层 `.venv` 启动探针会在本机报 `native MXC unavailable on this executor`，两个已试权限配置都受影响。直接基础解释器可激活 MXC，而且保留原虚拟环境作为被测对象。`--python` 给有不同安装目录的环境提供显式路径 |
| Windows 下用 `TemporaryDirectory(prefix='.mxc-unit-', dir=PROJECT)` 隔离出 `temp`、`links` 和临时测试入口。宿主用 `os.link`、`_winapi.CreateJunction` 生成真实对象 | 让准备阶段使用普通 OS 能力，内层 MXC 覆盖项目实际行为。无需为单测给 MXC 增加创建 junction 或 hardlink 的权限 |
| 内层脚本把项目路径加入 `sys.path`，设置测试 fixture 变量，以及 `TEMP`、`TMP`、`TMPDIR`、`tempfile.tempdir` | 直接执行临时入口时，项目模块仍需可发现。Python `tempfile` 可能缓存目录，且后端可能重设 inherited TEMP，故在内层同步设置环境变量及解释器缓存，保证 SQLite 等临时件落到本次专用项目目录 |
| 外层严格传入 `-c windows.sandbox=mxc sandbox --include-managed-config`、现有权限配置、项目目录和解释器。`--codex` 默认 `codex`，可用完整可执行路径覆盖。`--permission-profile` 默认 `:workspace`，完整 HTTP 单测可显式选用已具有 loopback 能力的配置 | 把验证绑定到实际后端与实际解释器。运行器本身不创建配置、不打开全局网络权限。网络测试必须以相应测试的权限边界来验收 |
| 内层用 `unittest.defaultTestLoader.discover` 发现 `tests/unit`，`TextTestRunner` 执行，根据 `result.wasSuccessful()` 返回 0 或 1。外层返回 Codex 进程真实退出码 | 修正早期分类探针只打印结果不设置退出码的问题。CI 或人工执行均能根据结果判断 |
| `finally` 先对 junction 本身 `rmdir`，再由 `TemporaryDirectory` 清理当前运行目录 | 避免递归清理路径沿 junction 遍历。原项目目录、原 `.venv` 及其他任务的临时目录均在清理范围之外 |

### 8.6 修改后实测及默认权限边界

| 返回时间 | 运行路径 | 结果 |
|---|---|---|
| 21:23:45 | 普通宿主，项目原 `.venv`，完整单测 | 202 项，25.992 秒，`OK (skipped=1)`，退出 0 |
| 21:23:56 | MXC，项目原 `.venv`，`step1-git`，宿主预建真实夹具 | 202 项，34.110 秒，`OK (skipped=1)`，退出 0 |
| 21:25 起 | 把外层验证入口也交给 `.venv` | 本机出现 `native MXC unavailable on this executor`。最终命令改用直接的基础 Python 启动外层，原 `.venv` 留在内层 |
| 21:30:44 | 基础 Python 外层，MXC，默认 `:workspace` | 202 项，32.034 秒，6 errors、1 skipped，退出 1。剩余为本地 HTTP `WinError 10013`，原五项没有复现失败 |

两套通过的 202 项中各有 201 项实际通过。唯一跳过继续是已有的真实 CLI 可选测试，本轮测试修改没有增加跳过条件。`:workspace` 的六项错误是 `test_explicit_broadcast_cannot_silently_lose_grok`、`test_get_only_reads_cache_and_rejects_foreign_host`、`test_grok_diagnostic_arms_only_first_matching_ordinary_message`、`test_non_directory_returns_json_error_for_get_and_post`、`test_probe_requires_origin_token_json_and_valid_payload`、`test_real_http_security_and_update`。该对照说明完整套件要按已有策略使用允许本地回环的权限配置。[HTTP 能力测试](../../tests/unit/test_capabilities.py) · [协作操作测试](../../tests/unit/test_collaboration_actions.py)

单测功能复测示例，从普通 PowerShell 切到项目根后执行。基础安装路径可由 `.venv\pyvenv.cfg` 的 `executable` 项核对。`step1-git` 是本机已经存在的权限配置。其他开发机可用自己的现有权限配置，结果应按其 loopback 能力解释。

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/unit

$basePython = '<BASE_PYTHON_EXE>'
$codex = '<CODEX_CLI_EXE>'
& $basePython -B .\tests\verify\run_mxc_unit.py --codex $codex `
    --permission-profile step1-git
$LASTEXITCODE
```

修改后还通过了 Python AST 语法检查及已跟踪差异的 `git diff --check`。测试脚本与文件仍处于本地修改状态。

## 9. 项目级 MXC 配置和真实工作台验收

### 9.1 实际落盘的配置

用户确认让工作台使用已验证路径后，本机于 `21:48:51` 新建项目根的 [.codex/config.toml](../../.codex/config.toml)，内容如下。

```toml
[windows]
sandbox = "mxc"
```

| 选择 | 原因与边界 |
|---|---|
| 使用已受信任项目中的 `.codex/config.toml` | 让本项目启动的 Codex 进程读取局部设置。其他目录和其他设备要分别满足配置加载和 MXC 可用条件 |
| 使用 `[windows] sandbox = "mxc"` | 这是 Windows 沙箱实现的选择，与应用任务所用文件系统／网络的 permission profile 分属不同设置。无需把 `mxc` 填进工作台的 `CODEX_SANDBOX` 或 CLI 的 `--sandbox` 模式参数 |
| 保留全局 `windows.sandbox=elevated`、用户已有 `step1-git`，配置中只写上面两行 | 针对本项目改变后端，沿用用户已有的文件和网络权限规则。无需编辑用户全局 TOML、改变 Windows ACL、复制已登录信息或给所有项目打开权限 |
| 仍由工作台本来的 Python 宿主和 Codex 启动适配执行 | 当前适配在每条调用启动新的 CLI 进程，传入实际工作目录，再执行新建或 `exec resume`。测试确认已运行的工作台无需重启即可让下一次命令读取项目配置。启动适配和最终汇总的 `read-only` 分支代码未修订，本轮未单独触发最终汇总做运行验收 |

后端是否生效，以发生这次运行的 Codex 启动记录为准。真实验收时有 `Windows sandbox selection: mxc`，同时工作台普通任务记录显示 `permission_profile_requested=step1-git`。仅看到配置文件存在、CLI 可识别配置字段或应用接收消息，还不能单独证明这次实际启用了 MXC。[Codex 配置基础](https://learn.chatgpt.com/docs/config-file/config-basic) · [Codex Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 9.2 四轮验收

从同一个在线工作台发真实 Codex 消息，先新建会话，再在原会话续接，验证停止后同会话仍可再次使用。四次普通任务均请求 `step1-git`。新建和续接各从实际启动记录确认 `mxc` 的后端选择，判定还要求实测解释器退出码和工作台退出确认。

| 工作台回复消息 | 北京时间及启动模式 | 核查的实际结果 |
|---|---|---|
| `4264` | 21:50:58，`new` | 启动项目原 `.venv`，`ctypes`、`ssl` 导入成功，`OpenSSL 3.0.16 11 Feb 2025`，`IMPORT_EXIT=0`。工作台运行 `completed`，`exit_confirmed=1` |
| `4266` | 21:52:42，同一 Codex 会话 `resume` | `ctypes`、`ssl`、`mcp` 均导入成功。MCP 真实来源为项目 `.venv\Lib\site-packages`，`IMPORT_EXIT=0`。运行 `completed`，`exit_confirmed=1` |
| `4268` | 21:55:09，`resume` 中停止 | 任务受控启动约 45 秒等待的带唯一标记的 Python 探针。停止之前观察到解释器启动链 PID `21296`、`1000`。对实际消息调用工作台的停止入口，仅命中 1 个运行。停止后检查两个进程均已退出，运行状态 `cancelled`、`cancel_requested=1`、`exit_confirmed=1` |
| `4270` | 21:56:51，停止后同会话再次 `resume` | 重新执行 `ssl`、`ctypes` 导入得到 `IMPORT_EXIT=0`。运行 `completed`，`exit_confirmed=1`。证明本次停止后同会话可以继续使用 |

一条更晚的消息 `4272` 在 22:04:56 被协作协议用 `duplicate JSON key: original_question` 拒绝，明确在计划复测命令执行之前结束。它没有产生新的 DLL 结果，不能与上述导入验收混计。本轮实际使用的停止范围为单条 Codex 运行。

### 9.3 交付和其他开发机的运行边界

这次同意并验证的是本项目本机的 MXC 配置。五个测试文件和项目配置处于本机工作树，未把它们提交或推送。普通宿主单测仍可直接运行，未设置 `COAGENT_LINK_FIXTURES` 时自建夹具。以后对外分发，需要按项目是否决定随仓库下发 MXC 配置、受信任项目的加载规则、Windows/CLI 支持范围分别确定首次启动说明。单机验证无法覆盖同事的系统构建、Codex 版本、网络或相同的命名配置。

其他环境复现时可按以下顺序收集证据。

1. 先记录工作台实际 Codex 启动模式、Windows 沙箱选择、权限配置、工作目录、Python 版本及路径。用相同环境下的最小导入探针辨别是否真的重现。
2. 如果两条 Python 路径使用同一基础安装，DLL 已成功映射但导入仍报 1114，可按第 4、5 节确认是否进入同一初始化链。尽量同时捕获一条成功对照。
3. 在支持 MXC 的环境先用一次性参数探测原 `.venv`。工作台采用项目配置后，还要跑真实 CLI 新建和续接，而不是把宿主直接启动成功当作网页验收。
4. 单测首先区分默认临时目录、链接准备和本地 HTTP 回环。按第 8 节使用宿主外层、实际 `.venv` 内层、已存在且与测试匹配的 permission profile。保留真实退出码、用例计数与唯一跳过原因。
5. 已验证 Windows DLL 导入、原虚拟环境 MCP、项目两套单测、工作台新建／续接／停止后续接。此前 Git 在其他后端的提交推送另有证据。MXC 下的真实 Git 提交与远端推送、最终汇总、整机重启后及其他机器的行为，要用对应运行单独验收。

## 10. 正式教训记录的形成依据

entry-4300 用户要求结合本调查和 entry-4290 三方复盘正式落地。拟在 `docs/lessons/WINDOWS_VENV_DLL_MXC_LESSONS.md` 收录以下内容，保留本稿 §1–9 作为原始调查基线。

- 事实与边界沿用 §3.3、§4–5 和 §8–9。原启动链的 User32 进程附加失败、同版本重建对照和本机 MXC 验收分别陈述。线程状态、桌面线索与跨机验证保留各自证据范围。
- 采用 entry-4290 的三方复盘。记录工作台 Codex 的原链复现与宿主取证限制、DeepSeek Harness 撤回重建建议、Grok 历史交接失败。排查步骤聚焦真实启动链、同链临时实例对照、宿主侧取证与入口级验收。
- 保留六项后续风险及验证条件。优先记录实际沙箱后端、迁移错误分类和 MXC 测试前提，其余覆盖 Grok 文件工具、Job 进程链、宿主与 Agent 能力探测。项目代码改进和剩余动态验收仍待执行。
- 同步草稿索引与正式教训索引，保留双向链接和变更历史。总索引已有教训分区入口。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-21 22:45:29 +08:00 | entry-4300／后继关联 | 追加正式稿形成依据→关联正式教训 1.0 | 用户授权及 §10，本机时钟 | Codex 整理 | 文首状态和后继链接 |
| 2026-11-21 22:44:47 +08:00 | entry-4300／正式稿基线 | 0.1 调查记录→追加正式稿形成依据 | 三方复盘及用户正式落地要求，见 §10。本机时钟 | 用户提出，Codex 整理 | 历史取证结论保留 |
| 2026-11-21 22:21:34 +08:00 | 0.1／ProcMon 与 WinDbg 复核 | 草稿待审阅→补正文件访问结果及进程创建证据，仍待审阅 | 对照捕获确认 `PATH NOT FOUND` 与 `NAME NOT FOUND` 的归属，补记两条进程创建事件。WinDbg 核心结论及证据边界维持 | 用户要求反馈复核结果，Codex 复核，本机时钟 | 正文第 4 节 |
| 2026-11-21 22:11:36 +08:00 | 0.1／Windows DLL 故障与 MXC 复盘 | 无→草稿待审阅 | 用户要求完整记录初始现象、定位过程、证据、测试调整原因及项目配置。正文保留实验的有效结果及边界 | 用户提出，Codex 整理，本机时钟 | 本草稿及草稿目录索引 |
