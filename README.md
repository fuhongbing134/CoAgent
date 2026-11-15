# CoAgent

## 1. 项目介绍

CoAgent 是一个本机多 Agent 网页工作台，通过命令行工具（CLI）调用 Codex、DeepSeek Harness 和 Grok。适合对 Agent 有基本了解、希望让多个 Agent 共同处理问题的人，包括工程师和非工程师。

你可以点名一个 Agent 提问，也可以让三端独立评估、交叉复核，再由负责人汇总。工作台提供统一对话入口、会话续接、带依赖的协作任务、审核与交接记录。项目规范会随任务入口提供，实际遵从仍需要执行和检视确认。

当一个问题需要多种判断时，难点还包括：谁先做、谁检查、哪些结论有证据，以及失败后是否还能继续。CoAgent 把分工、依赖和交接放在同一条工作流里，让你能查看各端结果和未决事项，再判断任务是否完成。比如评审一份上手说明，可以先分别检查操作步骤与功能事实，再交叉复核，最后汇总仍需修正的问题。

协作之外，工作台还记录执行占用和进程退出证据，减少同一执行域重复启动的风险；它会区分“取消已请求”“进程退出已确认”和“协作回复已接收”。这些状态不能互相替代，保障范围与限制见第5、6节。

- **快速开始走网页直调 CLI**：只需准备一个可用后端即可体验。
- **MCP 消息桥是可选接入**：用于客户端间登记和交换消息，不是启动工作台的前置条件。

服务默认监听本机地址；各后端仍按自己的登录与网络配置访问模型服务。

## 2. 环境要求与快速开始

当前执行实现要求 Windows 10+，以下命令使用 PowerShell。需要 Python、至少一个已安装且能独立工作的后端 CLI，以及该后端所需的账户/认证。最低 Windows 版本检查不等于各版本均已实测；Python 最低支持版本和 CLI 兼容范围尚未建立验证矩阵。项目 Python 依赖见 [requirements.txt](requirements.txt)，不包含三个后端 CLI。

### 获取项目并安装依赖

在 PowerShell 中进入你希望保存项目的父目录，执行（已下载项目可直接进入项目目录）：

```powershell
git clone https://github.com/fuhongbing134/CoAgent.git
cd CoAgent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

没有 Git 时可从仓库页面下载 ZIP 并解压，再在包含本文件与 `web_chat.py` 的目录打开 PowerShell。后面的命令均在该项目目录执行，不需要激活虚拟环境。

### 选择一个后端

先通过对应产品的安装和登录流程准备 CLI，并在同一个 PowerShell 中检查所选命令：

| 后端 | 准备与检查 |
|---|---|
| Codex | 按 [官方安装说明](https://github.com/openai/codex)安装并完成 Codex CLI 认证；运行 `codex --version`，再确认在命令行中能够正常对话 |
| DeepSeek Harness | 从 [官方项目入口](https://github.com/deepseek-ai/deepseek-harness)查安装方式；使用桌面版时，在菜单选择 **Manage dsh Command… → Install**，重新打开 PowerShell，运行 `dsh --version` 并确认已登录；工作台使用 `headless` profile |
| Grok | 按 [官方 CLI 安装与认证说明](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/npm/grok/README.md)准备 `grok` 命令；运行 `grok --version`，再确认命令行对话可用 |

`--version` 成功仅证明命令可启动，不证明认证和模型调用成功。CLI 不在 PATH 时，按第4节设置完整路径。DSH 的 [dsh-headless-auth.patch.yml](dsh-headless-auth.patch.yml) 是插件开关配置，允许 headless 使用已保存认证；它不包含登录凭据，也不替代登录。干净环境下的认证效果仍需自行确认。

### 启动并完成第一次对话

```powershell
.\.venv\Scripts\python.exe .\web_chat.py
```

看到“Agent 工作台已启动”后，在浏览器打开 <http://127.0.0.1:8765>。保留启动它的 PowerShell 窗口。

在输入框发送 `@Codex 请用一句话介绍你自己。`；如果你准备的是另一个后端，将点名换为 `@DeepSeek Harness` 或 `@Grok`。点名会进入任务入口；简单问题可以直接答完，无需展开多步计划。该条消息成为完成态答复，即完成首次体验。若显示失败或协议不兼容，请按第5节排查，不能把已有正文输出当作成功。之后可继续追问检查会话续接。没有回复时，先查看错误提示与能力状态中的 CLI 检测结果；检测结果的范围见第5节。

端口占用时可用 `.\.venv\Scripts\python.exe .\web_chat.py --port 8766`，并打开相应端口。在启动窗口按 **Ctrl+C** 关闭服务；程序会调用已跟踪活动运行的清理逻辑，不能据此保证所有外部派生进程均已退出。

## 3. 基本使用与协作示例

### 点名一个身份

```text
@Grok 请解释这个项目的用途，先不要修改文件。
```

替换成已配置的身份即可。正文中第一个有效点名决定入口，完整原文交给该身份；负责人和最终答复者由任务语义与计划确定。同一条消息列出多个身份不会自动形成并行分工。

### 广播，各自回答

```text
@all 请各自用一句话说明你的看法。
```

没有点名的消息也按广播处理，发送给广播名单中的已启用身份。各自独立回答，不建立带任务依赖和负责人审核的协作计划；未配置的后端可能报错，所以第一次使用建议点名单个身份。

### 按计划共同完成工作

```text
@Codex 请组织三端评估这个项目的上手说明：先独立评估，再交叉复核，最后由你汇总。只讨论，不修改文件。
```

需要参与的后端均已配置。入口形成任务与依赖，调度器依次执行，负责人审核交接后继续或收口。同一协作 job 当前每次只运行一个就绪任务。Codex、DeepSeek Harness、Grok 是后端身份；入口、协作者、负责人、最终答复者是一次任务中的角色，同一身份可以先后担任不同角色。

协作成功应看到计划任务结束、有效交接被接收和最终答复；仅有CLI输出正文不足以证明协议通过。交接提供原始问题、结果或改动、证据、未采纳方案、未决事项、下一步，帮助判断进展。详细流程见 [docs/architecture/WORKBENCH_CALL_FLOW.md](docs/architecture/WORKBENCH_CALL_FLOW.md)。

## 4. 配置、权限与数据

在启动工作台的 PowerShell 中设置环境变量，示例中的路径须替换为实际路径：

```powershell
$env:CODEX_CLI = "C:\tools\codex.exe"
# 仅设置你需要覆盖的后端；其他选项是 DSH_CLI、GROK_CLI。
.\.venv\Scripts\python.exe .\web_chat.py
```

| 配置 | 当前查找或用途 |
|---|---|
| `AGENT_BRIDGE_DB` | SQLite 路径；默认项目目录 `agent_bridge.db` |
| `CODEX_CLI` | 显式值优先；未设置时查 PATH 的 codex，再查本机 Codex 桌面安装目录，按修改时间取最新候选 |
| `DSH_CLI` | 默认 dsh；对配置值先查 PATH，再检查其对应文件路径 |
| `DSH_HEADLESS_PATCH` | 默认项目内 `dsh-headless-auth.patch.yml` |
| `GROK_CLI` | 显式值优先；未设置时查 PATH 的 grok，再查用户目录 `.grok/bin/grok.exe`（Windows） |

Codex/Grok 显式路径无效时不会继续回退到默认候选。修改 PATH 后重新打开 PowerShell并重启工作台。

页面可为每个对话选择已存在的工作目录；该对话有 Agent 正在运行时不能切换工作目录，对话空闲时可以更改。切换后保留共享消息，在新目录建立后端会话。工作台对话与后端 CLI session 是两套标识。

权限由后端自身配置和工作台启动参数共同决定，各后端并不等价。当前 Codex 常规使用 `workspace-write`；DSH 使用 headless profile；Grok常规使用 `auto` 并禁止子Agent。只有系统额外启动的最终交付轮次（`final` 角色）才使用额外只读/工具限制；负责人自行收口仍是 owner 角色，不自动增加这些限制。Grok当前没有加入 `--trust` 或 `--allow Bash`。

消息、会话关联与执行状态在本地持久化。工作台使用 `workbench_messages`，可选桥接使用 `agents`/`messages`；普通客户端聊天不会自动完整导入。提示中可能包含桥消息和共享对话：共享上下文默认最近30条、每条约3000字符截断，并有已见消息过滤，并非完整历史原文。发送给Agent的内容可能随CLI请求进入对应模型服务，请按各后端的数据政策选择分享内容。

需要MCP客户端配置时再阅读 [MCP_SETUP.md](MCP_SETUP.md)。

## 5. 常见问题与已知限制

| 现象 | 先检查 |
|---|---|
| 找不到 CLI | 第4节路径、环境变量及启动窗口的PATH；显式配置是否指向有效文件 |
| CLI存在但对话失败 | 先在独立CLI中确认认证与模型访问；DSH同时检查headless配置 |
| 端口或数据库占用 | 换空闲端口；确认没有另一个服务使用同一数据库，不要删除锁文件强行接管 |
| 会话待校验或任务阻断 | 按页面错误和执行记录排查；维护者可用第7节只读检查入口 |
| 协议回复被拒绝 | 查看任务错误；正文输出不等于合法协作交接，协议需通过接收校验 |

“检测工作台能力”会主动探测宿主进程的目录读写、Git/网络及CLI可用性；自动刷新只读缓存，结果约5分钟过期。它不证明Agent沙箱权限、认证或模型可用；Agent沙箱实际能力显示未检测。网络探测会访问外部站点，按需点击。

服务中断后，执行占用可能保守保留；重启初始化会把遗留的 pending 消息标为 cancelled，这不证明旧进程已经退出，也不代表原任务会自动续跑。目前没有崩溃遗留占用的自动解除或正式人工解除工具，不能把重启当作恢复完成。

干净环境安装、最低 Python 版本、CLI 兼容矩阵、真实模型调用、MCP 真实登记及浏览器协作全流程尚无完整验收结论。架构中的改进方向和 [BACKLOG.md](BACKLOG.md) 是后续事项，不能当作已交付能力。

## 6. 架构概览与项目结构

浏览器通过本机HTTP服务与Python宿主通信；宿主启动外部CLI、管理会话与协作任务，并在SQLite记录消息和状态。MCP桥另行通过stdio供客户端调用，可使用同一数据库。

执行与回复有三条需要分清的边界：

- **占用不等于存活。** 同一后端、同一工作台对话的执行域通过数据库条件更新认领，避免两个受管执行同时持权；占用记录本身不证明进程还在运行。
- **取消不等于退出。** Windows Job Object 管理本次启动的普通后代；只有退出证据为 `confirmed` 或 `not_started` 才允许释放执行域。WMI、服务或既有外部进程承接的工作不在此覆盖内，不能保证所有外部写入者都已停止。
- **输出不等于接收。** 协作成功输出先暂存 pending，再由协议接收事务决定 completed、failed 或 cancelled；协议拒收会落 failed 并附原因。普通回复、失败和取消另有直接回写路径，pending 不是终态。

session 绑定代次校验已有数据层实现，但默认配置未启用统一 session 校验；不能将它写成默认生产保障。共享 session 身份检查也存在动态绑定和外部写入的覆盖边界。详细机制及已知缺口见 [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)，历史验证与被否决的跨崩溃恢复方案见 [postmortem/P0-2_POSTMORTEM.md](postmortem/P0-2_POSTMORTEM.md)。

| 入口 | 用途 |
|---|---|
| [agent_bridge.py](agent_bridge.py) | 可选MCP消息桥 |
| [agent_registry.py](agent_registry.py) | 后端身份与别名 |
| [docs/architecture/README.md](docs/architecture/README.md) | 架构及调用流程导航；注意各文档基线和验证范围 |
| [main.py](main.py) | 独立Codex终端对话脚本，区别于网页工作台 |
| [web_chat.py](web_chat.py) | 网页工作台启动入口 |

## 7. 开发、测试与文档导航

网页上手以本 README 为准；仅在需要 MCP 客户端时阅读 [MCP_SETUP.md](MCP_SETUP.md)。维护者先读项目入口与规范，再按主题查架构或设计。

| 文档 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | Agent任务入口 |
| [BACKLOG.md](BACKLOG.md) | 待实现事项 |
| [docs/README.md](docs/README.md) | 设计、架构与草稿分区导航 |
| [specs/README.md](specs/README.md) | 生效规范索引 |

在项目目录运行能力模块专项隔离测试：

```powershell
.\.venv\Scripts\python.exe -m unittest test_capabilities -v
```

该命令不是全仓测试。安装Node.js时还会执行简化DOM的前端模拟回归，未安装则跳过；不等于浏览器视觉验收。[verify_real_backends.py](verify_real_backends.py) 仅做真实CLI `--version` 启动与退出探测，不验证模型调用、续接或完整取消流程。不要将它当作首次问答验收。

其他核心测试可按主题阅读；下列入口不是已通过的当前验收声明：

| 主题 | 测试入口与范围 |
|---|---|
| 协作 | [test_collaboration_entry.py](test_collaboration_entry.py)、[test_collaboration_lifecycle.py](test_collaboration_lifecycle.py)、[test_collaboration_service.py](test_collaboration_service.py)：入口、接收与调度 |
| 执行状态与接线 | [test_execution_sessions.py](test_execution_sessions.py)、[test_execution_tracking.py](test_execution_tracking.py)、[test_execution_wiring.py](test_execution_wiring.py)：session、执行域及隔离数据库/模拟调用接线 |
| 受管进程 | [test_managed_process.py](test_managed_process.py)：真实 Windows 受控进程与 Job 句柄，不证明真实后端没有向外部委托工作 |

复盘中的“17项验收、51项回归”是对应阶段的历史记录，不代表每次修改后已重新验证；当前检查与未验证项见设计记录。

维护者可阅读 [inspect_execution.py](inspect_execution.py) 的只读执行诊断入口；数据库占用记录不证明操作系统进程仍然存活。本文内容方案和验证记录见 [docs/design/PROJECT_README_DESIGN.md](docs/design/PROJECT_README_DESIGN.md)。

## 8. 许可证与贡献方式

项目计划面向所有人公开。作者希望允许自由使用、修改、分发和商用，且不要求保留声明；**具体许可证尚未确定，仓库尚无 LICENSE 文件**，上述意向不代替正式授权文本。候选为面向软件的 [Unlicense](https://unlicense.org/)，备选为 [CC0](https://creativecommons.org/publicdomain/zero/1.0/)；第三方依赖仍遵循各自许可。

欢迎提交问题和改进建议。参与修改前请阅读项目入口与规范，说明改动目的、实际验证结果及剩余缺口；请勿提交凭据、本机运行数据库或私有会话。
