# Codex 与 DeepSeek Harness 消息桥

## Grok Build 接入补充（2026-11-01）

工作台调用 Grok CLI 与 MCP 消息桥是两条链路；CLI存在不证明桥接已登记。
Grok 启动命令可用 `GROK_CLI` 指定，否则检查PATH和用户目录 `.grok/bin/grok.exe`。
普通调用使用auto，接受非交互分类器拒绝工具、不等待人工批准且不保证工具执行；普通与最终均禁子Agent。最终交付dontAsk并拒绝全部工具，不宣称Windows操作系统级沙箱。

按[官方MCP说明](https://docs.x.ai/build/features/mcp-servers)，可在用户自己的 `~/.grok/config.toml` 中手动追加（不要覆盖已有配置）：

```toml
[mcp_servers.agent_bridge]
command = "C:/Users/FHB/Documents/Codex/agent-lab/step1/.venv/Scripts/python.exe"
args = ["C:/Users/FHB/Documents/Codex/agent-lab/step1/agent_bridge.py"]
env = { AGENT_BRIDGE_AGENT_ID = "grok", AGENT_BRIDGE_DB = "C:/Users/FHB/Documents/Codex/agent-lab/step1/agent_bridge.db" }
```

确认本机路径后由用户配置；本轮不写用户配置，不查询真实登记状态。
重新加载后，在普通协作阶段调用register_agent，核对返回agent_id为grok，再测试两端收发。
最终交付阶段拒绝工具，不应依赖该阶段进行MCP注册。
隔离测试证明通用桥接支持grok身份，不代替真实MCP连接验收。
具体修正及验证范围见[适配设计](docs/GROK_ADAPTATION_DESIGN.md)。

这个本地 MCP 服务让多个 Agent 通过同一份 SQLite 消息记录互相登记、发消息和读取对话。它只共享通过消息桥发送的内容，不会读取双方完整的私有聊天记录。

## Git 工作台适配器（需宿主显式配置）

新增 `git_workbench.py` 使用同一 MCP 依赖。为 Codex、DeepSeek Harness、Grok 分别增加 stdio 服务：command 为现有 Python，args 为 `["项目绝对路径/git_workbench.py", "--config", "仓库外/host.json", "--executor", "codex"]`；其他两个 executor 分别使用 `deepseek-harness`、`grok`。沿用各客户端上述/下述 MCP 配置格式，不覆盖消息桥。本次没有安装这些用户配置。

正常权限的 MCP 宿主是执行入口。配置服务不会授予 OS 权限；沙箱内运行仍受原限制，不能借本入口绕过 `.git` 只读。操作者应先在隔离仓库验收权限，并保证同一时间一个功能占用目录。

宿主配置示例（路径均替换为真实绝对路径；不向 Agent 开放此文件写权限）：

```json
{
  "database": "C:/host/agent_bridge.db",
  "approvals": "C:/host/git-approvals",
  "results": "C:/host/git-results",
  "tasks": {
    "task-id": {
      "job_id": "job-id",
      "directory": "C:/project",
      "branch": "feature/example",
      "base_commit": "完整40或64位commit ID",
      "remote": "origin",
      "executors": ["codex", "deepseek-harness", "grok"]
    }
  }
}
```

目录必须已存在。任务 ID 和 job ID 必须与工作台真实记录一致，对话必须保存显式工作目录；适配器只读数据库，不创建或迁移表。任务的 author 从数据库取得。配置及两个状态目录均放在任务仓库外，并以 ACL 保护；executor 是受信启动配置，不是凭证。配置在进程启动时读取，修改任务绑定后须重启适配器；数据库停止状态和目录每次调用重新核对。

审核完成后，宿主/reviewer 在 approvals 下保存 `<task-id>.json`，字段为 `task_id, branch, base_commit, author, reviewer, approved:true, content`。非作者 reviewer 核对实际差异后，使用同一配置实例的 `host.api.content(task_id, files)` 取得 content（文件名到模式/blob ID 的映射，删除为 null）；此只读辅助调用不构成批准，不能由作者自行写入批准。MCP 不开放此辅助函数或审批入口。宿主负责保存审核证据和只读 ACL，不自动把任务 completed 当作审核通过。

执行结果仅写入 results 下对应 JSON，approval 文件保持原样；批准变动而已有执行记录时拒绝，交宿主核实。只注册 `create_branch(task_id,name)` 和 `commit_and_push(task_id,branch,files,message)`。调用异常由 MCP 标记失败；不要把缺批准文件的错误当作可以跳过审核。

### Git 部署前必须确认（entry-2205，仅材料，未部署）

本文模板不含凭据，不代表已授权修改权限或推送。操作者逐项填实后再部署：

| 待确认项 | 要求 |
|---|---|
| database 实际路径 | 当前工作台使用的 agent_bridge.db，可能被 AGENT_BRIDGE_DB 覆盖；不新建空库冒充生产库，不编辑表来伪造任务 |
| host.json、approvals、results 实际位置 | 使用绝对路径，均在任务仓库外，三个路径不得混用；approvals/results 目录预先创建且分离 |
| job_id / task_id / directory | 来自工作台已安排任务；对话有显式目录设置，且解析结果与绑定 directory 相同；task_id 只能包含字母、数字、下划线、连字符，长度1–100 |
| branch / base_commit | branch 必须以 feature/ 开头；base_commit 为该仓库真实存在的完整小写40/64位十六进制 commit ID，不是分支名或缩写 |
| remote / executors | remote 是仓库已有远端名，不是 URL；查询和推送地址须唯一且相同。executors 只填实际获安排的 codex、deepseek-harness、grok，不必默认全开 |
| 执行与审核身份 | 谁运行 MCP、谁保存批准、谁维护配置及结果；明确实际 OS 身份和有效访问权限，保证一个功能独占目录 |

上方 JSON 是 host.json 模板，替换全部示例值后以 UTF-8 保存。任务作者由数据库读取，不在配置里添加 author 来覆盖。已有功能分支不能再次调用 create_branch；提交时 HEAD 必须对应本次审核基线，不能把旧审核移到新基线。

### 三个 MCP 客户端的启动模板

下面是三个服务的 **command/args 启动描述**，不是声称所有客户端都接受同一种 JSON 配置文件。按本文各客户端配置格式，分别注册一个名为 workbench-git 的 stdio 服务，保留原有 agent-bridge；若界面只接受字段，逐项填入。路径均为示例，Python 必须已安装项目依赖。

```json
{
  "codex": {
    "command": "C:/Users/FHB/Documents/Codex/agent-lab/step1/.venv/Scripts/python.exe",
    "args": ["C:/Users/FHB/Documents/Codex/agent-lab/step1/git_workbench.py", "--config", "C:/host/host.json", "--executor", "codex"]
  },
  "deepseek-harness": {
    "command": "C:/Users/FHB/Documents/Codex/agent-lab/step1/.venv/Scripts/python.exe",
    "args": ["C:/Users/FHB/Documents/Codex/agent-lab/step1/git_workbench.py", "--config", "C:/host/host.json", "--executor", "deepseek-harness"]
  },
  "grok": {
    "command": "C:/Users/FHB/Documents/Codex/agent-lab/step1/.venv/Scripts/python.exe",
    "args": ["C:/Users/FHB/Documents/Codex/agent-lab/step1/git_workbench.py", "--config", "C:/host/host.json", "--executor", "grok"]
  }
}
```

即 `python git_workbench.py --config <host.json> --executor <身份>`，三个客户端各自固定身份。executor 是受信启动参数，不是授权凭证；工具调用参数不能改变它。重载客户端/服务后进行普通工具调用阶段的验收，不在禁止工具的最终答复阶段测试。仅启动进程或看到 CLI 路径不算 MCP 连接成功。此入口运行在 MCP 进程，不是网页主进程；网页能力检测的成功不能代替这个进程的权限。

### 审批与结果目录的有效 ACL

| 对象 | reviewer / 宿主 | Git execution 进程 | 编码作者的 Agent 环境 |
|---|---|---|---|
| host.json | 受信维护者可写 | 读取 | 不可修改 |
| approvals 目录及其文件 | 审核后创建、修改批准 | 读取，不能写入/替换/删除批准 | 不可自行填 approved 或改审核内容 |
| results 目录及其文件 | 可检查和维护结果 | 可创建、写入、原子替换结果文件 | 不应获准伪造 operation |

在宿主上按实际 OS 账户设置并检查有效权限，检查父目录继承、组成员权限和文件所有者权限；只设置一个文件的“只读”属性不等于完成 ACL。results 需要目录写权限以支持临时文件和原子替换，不能只给已有文件写权限。数据库只读访问仍可能需要其运行模式所需的 SQLite 辅助文件访问，遇到拒绝先诊断，不为本入口更改生产库模式。

**三个 Agent 名称不是三个 Windows 安全主体。** 若 reviewer、作者、execution 全部使用同一 OS 账户且相同权限，ACL 无法按 Agent 名称区分；不能宣称“作者不能批准自己”已形成 OS 隔离。需由宿主确认独立的受限运行身份或已有隔离边界，确保作者不能直接写批准/结果，execution 只能读批准。当前适配器不创建账户、不配置这种隔离；未满足时记录为部署缺口，不用 --executor 冒充隔离。

非作者完成实际审核后，由宿主将前文规定的批准字段写入 approvals/<task-id>.json；content 取同一任务、同一文件清单的真实 blob/mode，不能手抄伪造。批准文件不含 operation。执行仅在 results 写回 commit_id/push_status。已有 operation 时不要为“重试”删除结果文件：先核对本地 commit 和远端；同参数重复调用只补推，不另提交。更新配置需重启服务。

### 部署后的分层验收清单（当前均未执行）

先用测试专用任务、隔离仓库和本地 bare 远端，避免改动现有功能目录；随后在用户明确指定的真实仓库与远端验收。下列写操作是部署后的验收步骤，本次文档任务不执行。

| 编号 | 操作与应保留的证据 | 通过条件与限制 |
|---|---|---|
| D1 进程与权限 | 记录 MCP 实际运行账户、解释器、配置路径及任务仓库；由该 MCP 入口调用 create_branch(task_id, name)，name 等于绑定的 feature/ 分支，工作区原先干净 | 返回预期基线、分支与 HEAD，实际分支存在。只能证明该进程对该仓库的写权限；在临时仓库成功不能证明项目 .git 可写 |
| D2 真实推送 | 在测试功能分支做小改动，经非作者审核并保存批准，调用 commit_and_push(task_id, branch, files, message)；保留返回 commit_id、push_status，核对远端同名 refs/heads/feature/... 的 commit | push_status=succeeded，远端提交与返回一致，推送前后 main 引用不变。用本地 bare 通过仅证明本地链路，不能记作 GitHub 认证验收；真实 GitHub 须另外授权并实际核对 |
| D3 三客户端连接 | Codex、DeepSeek Harness、Grok 分别从客户端完成 stdio 初始化与 tools/list，保存服务名称、工具列表及脱敏错误；依次使用各自测试任务调用工具 | 每个服务都恰好列出 create_branch、commit_and_push，两种调用按任务范围返回。内部 FastMCP 分发测试和单客户端成功不代替三客户端实测 |
| D4 缺审核与自审 | 在隔离测试任务分别使用缺批准文件、reviewer=author 的批准记录；调用前后读取 HEAD 和远端目标；后一个场景只能由宿主准备负例文件 | 两者均拒绝，HEAD 和远端不变。缺文件可能以 FileNotFoundError/MCP工具错误呈现，不能因此跳过审核 |
| D5 main / force / URL | 调用 create_branch(...,"main")、对 commit_and_push 传 branch="main"；尝试额外 force 或 URL 参数；另用宿主准备的隔离配置将 remote 设成 URL | main 与 URL 配置被拒，force/URL 不是工具支持参数，不能触发额外能力；本地和远端 main 均不变。工具没有任意 git 命令入口，不为验证而真正强推 main |
| D6 ACL | 使用 execution 实际身份检查批准目录无法新建、替换或删除批准，results 可正常原子写入；使用作者环境检查不能伪造批准/结果 | 在宿主专用测试文件上验证有效权限，不改真实批准作探针；同账户无隔离时不能填写通过 |

对 D5 不预先断言 SDK 必定拒绝未知字段：若客户端/SDK 忽略了额外 force/URL 字段，只能记为“未开放该能力”，不能伪记为“参数拒绝已验证”；需核对实际工具 schema、调用返回和远端引用，确认没有强推或改目标。要验收严格的未知字段拒绝，需要另行核查当前 SDK 行为；本轮未测试也未修改参数校验代码。

隔离回归可由操作者在确认环境后运行 `python -B -m unittest test_git_workbench test_git_automation -v`（临时仓库、本地 bare 远端）。它不连接三个实际客户端，也不验收生产仓库权限。本次未运行该命令。

若使用真实仓库探针，先明确授权测试分支名称及基线，确认无人编辑或运行任务；不要自动 stash、reset 或丢弃改动。工具不提供删除分支：测试完成后保留证据，清理须另行确认，由操作者显式进行；不要将自动删分支混进部署脚本。合入 main 不在验收范围。

每项记录“未执行/通过/失败/受阻”、时间、进程身份、代码 commit、任务绑定、操作结果及限制；不记录令牌、密码或带凭据 URL。任一真实验收未完成，就保留相应缺口。停用时移除本服务配置并重载客户端即可；保留用户提交与结果供检查，不自动回滚历史。

## 1. 安装依赖

在 PowerShell 进入本目录后运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 将服务接入 Codex

当前电脑已在 Codex 用户级配置中注册 `agent-bridge`。重启 Codex 后，它会启动本项目的 MCP 服务。若要在另一台电脑上配置，可运行：

```powershell
codex mcp add agent-bridge `
  --env "AGENT_BRIDGE_AGENT_ID=codex" `
  --env "AGENT_BRIDGE_DB=C:\Users\FHB\Documents\Codex\agent-lab\step1\agent_bridge.db" `
  -- "C:\Users\FHB\Documents\Codex\agent-lab\step1\.venv\Scripts\python.exe" "C:\Users\FHB\Documents\Codex\agent-lab\step1\agent_bridge.py"
```

上面命令里的 Python 可执行文件应为：

```text
C:\Users\FHB\Documents\Codex\agent-lab\step1\.venv\Scripts\python.exe
```

添加后重启 Codex 客户端，让它重新载入 MCP 配置。

## 3. 将服务接入 DeepSeek Harness

当前电脑已在 DeepSeek Harness 桌面 profile 安装 `@deepseek-ai/dsh-mcp-client`，并将下面这个条目追加到了配置中。重启 DeepSeek Harness 后，它会启动同一个 MCP 服务，并使用同一个 SQLite 数据库：

```text
C:\Users\FHB\.dsh\profiles\desktop\cordis.patch.yml
```

配置条目：

```yaml
- insert:
    - id: mcp-agent-bridge
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: agent_bridge
        transport: stdio
        command: "C:\\Users\\FHB\\Documents\\Codex\\agent-lab\\step1\\.venv\\Scripts\\python.exe"
        args:
          - "C:\\Users\\FHB\\Documents\\Codex\\agent-lab\\step1\\agent_bridge.py"
        env:
          AGENT_BRIDGE_AGENT_ID: deepseek-harness
          AGENT_BRIDGE_DB: "C:\\Users\\FHB\\Documents\\Codex\\agent-lab\\step1\\agent_bridge.db"
        cwd: "C:\\Users\\FHB\\Documents\\Codex\\agent-lab\\step1"
```

如果配置项报 `UNKNOWN_PLUGIN` 或类似“找不到 `@deepseek-ai/dsh-mcp-client`”的错误，需要先通过 DeepSeek Harness 的插件管理器安装这个官方 MCP Client 插件，再重新加载桌面 profile。配置 YAML 中已有其他内容时，只追加该条目，不要覆盖原有内容。

## 4. 让双方互相登记并发送第一条消息

在两个 Agent 的首条任务里分别要求它们调用 `register_agent`：

- Codex：`register_agent(name="Codex", vendor="OpenAI", model="当前 Codex 模型", role="协作与代码审查")`
- DeepSeek Harness：`register_agent(name="DeepSeek Harness", vendor="DeepSeek", model="deepseek-v4-pro", role="协作与规划")`

双方都登记后，调用 `list_agents` 应能看到对方。然后用 `send_message` 发消息，用 `read_inbox` 或 `read_conversation` 查看消息。

## 消息桥工具

- `register_agent`：登记当前客户端身份。身份 ID 由 MCP 配置中的 `AGENT_BRIDGE_AGENT_ID` 决定。
- `list_agents`：列出已登记的客户端及其最近活动时间。
- `send_message`：给另一 Agent 发一条消息。
- `read_inbox`：读取发给当前 Agent 的消息。
- `read_conversation`：读取当前 Agent 参与的最近对话。

SQLite 文件默认放在本目录的 `agent_bridge.db`。两边必须使用同一个数据库路径，且通过 MCP 工具登记和交换内容。服务不提供跨机器网络访问，也不会自动转发普通聊天内容。

## 5. 启动统一聊天界面并与 Codex、DeepSeek Harness 对话

该页面显示已登记的 Agent 和经消息桥发送的消息。工作台会把用户消息和回复保存在同一数据库的 `workbench_messages` 表，并为每个工作台对话、每个 Agent 单独续接会话。原有的 `agents` 和 `messages` 桥接表不会被工作台改写。客户端里的其他普通聊天不会自动导入。

在统一聊天框中，不写 Agent 名称或写 `@all` 会同时发送给 Codex 和 DeepSeek Harness；`@Codex` 或 `@OpenAI` 只发送给 Codex；`@DeepSeek Harness`、`@DeepSeekHarness` 或 `@DeepSeek` 只发送给 DeepSeek Harness。输入 `@` 后会显示候选，可点击候选，或用上下箭头选择后按 Tab 补全；按 Enter 会发送当前输入内容，不会自动替换或补全。同一条消息提及多个 Agent 时支持两种写法：以 `@Agent` 开头时，该提及到下一个提及之间的内容发给前一个 Agent；以普通文字开头时，每段文字发给其后紧跟的 `@Agent`，最后一个提及后的文字也发给该 Agent。按 Esc 或点击输入框外可以关闭候选，删除同一个提及时不会反复弹出，继续输入新字符后会重新联想。邮箱地址、代码片段和转义的 `\@` 不会触发候选或路由。一个 Agent 正在处理时，仍可给另一个 Agent 发消息；同一工作台对话中的同一个 Agent 不会同时处理两条消息。

向 Codex 发送时，工作台会把所选目录作为 Codex CLI 的工作目录。运行权限遵循 Codex CLI 自身的默认配置。工作台先查找 PATH 中的 `codex`，再自动查找 Codex 桌面应用的安装目录；如果你的安装位置不同，可以在启动前设置 `CODEX_CLI` 为 Codex 可执行文件的完整路径。这样不需要把 Codex 的内部安装目录写入 PowerShell 的 PATH。

向 DeepSeek Harness 发送时，工作台通过 headless CLI 启动一个无界面 DSH 运行，并用 `--session-id` 续接该对话。首次使用前，在 DeepSeek Harness 桌面应用菜单中选择 **Manage dsh Command… → Install**，然后重新打开 PowerShell，确认 `dsh --version` 能运行。若 CLI 未注册到 PATH，也可以在启动工作台前设置 `DSH_CLI` 为 dsh 命令的完整路径。

如果 `dsh` 指向 DeepSeek Harness 桌面版自带的 `dsh.cmd`，工作台会直接调用 Electron 包内的 DSH CLI，并设置 `ELECTRON_RUN_AS_NODE=1`，绕过桌面 host 启动层。非桌面版安装或无法识别桌面安装结构时，工作台仍使用配置的 `dsh` 命令。

工作台使用 DSH 的 `headless` profile，并为每条工作台对话单独保存 session ID。凭据与认证 patch 从当前 DSH_HOME 和 `dsh-headless-auth.patch.yml` 加载。

`dsh-headless-auth.patch.yml` 会为 headless profile 重新启用 DSH 的凭据、授权和 DeepSeek 账户插件，以便它使用 DSH 已保存的登录信息。该配置覆盖层只应用于工作台启动的 headless 进程，不会改写桌面 profile 配置。headless 会话与桌面应用中的普通会话分开保存；桌面聊天记录不会自动导入。headless profile 也不会自动继承桌面 profile 的 MCP 插件配置。

在项目目录打开**普通 Windows PowerShell**，运行：

```powershell
.\.venv\Scripts\python.exe .\web_chat.py
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

工作台只负责设置 CLI 的工作目录，不会覆盖 Codex CLI 或 DeepSeek Harness 的权限预设。两者按各自当前 profile 和 CLI 配置运行。

### 工作目录

页面上方的“当前对话的工作目录”可为所选对话指定一个已存在的绝对路径。默认目录是本项目目录。路径按对话保存；更改时如果 Agent 正在运行，工作台会拒绝更改。更改成功后，该对话的共享消息保留，Codex 与 DeepSeek Harness 会在新目录启动新的 CLI 会话。

Codex 和 DeepSeek Harness 都会以该路径作为 CLI 工作目录。工作台不为 Agent 配置或切换权限预设；实际权限由各自 CLI/profile 的配置决定。

页面每 3 秒刷新一次。思考中的回复下可停止该 Agent，发送按钮旁的按钮可停止所有正在运行的 Agent；关闭工作台时也会终止其 CLI 进程。按 `Ctrl+C` 停止服务。默认使用本目录中的 `agent_bridge.db`；如果 MCP 服务配置了其他 `AGENT_BRIDGE_DB` 路径，启动网页前也要在当前 PowerShell 设置同一个值：

```powershell
$env:AGENT_BRIDGE_DB = "C:\path\to\agent_bridge.db"
.\.venv\Scripts\python.exe .\web_chat.py
```

若在安装 DSH CLI 之前已经启动了网页服务器，先按 `Ctrl+C` 停止，再从新开的 PowerShell 窗口重新运行工作台，使它能读取更新后的 PATH。

# 工作台能力状态（第一版）

侧栏“能力状态”展示工作台进程的实测结果和 Agent 的静态启动配置。
点击“检测工作台能力”才会触发后台探测；页面自动刷新只读取缓存。
需要重启工作台加载新功能。

- 工作目录：创建随机临时文件、读回、删除；不写入 `.git`。
- Git 本地读取：`git status`，关闭可选锁及 fsmonitor。
- HTTPS：分别向 `github.com` 和 `chatgpt.com` 发送 HEAD，不跟随重定向。
  HTTP 403 等响应单独显示，不等于连接失败，也不代表模型/MCP 接口正常。
- 远程读取：仅检测无内嵌凭据的 GitHub HTTPS origin，存在 Git URL 重写时跳过；
  使用非交互 `git ls-remote`，不提交、不推送，读取成功不代表有推送权限。
- 结果绑定规范化后的工作目录，保存在内存；5 分钟后标为过期，重启清空。
  全局最多一个检测任务，同目录重复请求复用运行中的任务，完成后 30 秒内禁止重测。
- Agent 沙箱实际能力始终显示“未检测”，不能用工作台结果代替。

新增接口：`GET /api/capabilities?conversation_id=...` 读取缓存；
`POST /api/capabilities/probe` 触发检测。后者要求同源 Origin、JSON 和页面随机令牌，
不接受客户端自定义目标 URL。此保护仅覆盖新功能，现有写接口尚未统一加固。

测试：`python -m unittest test_capabilities -v`。
如已安装 Node.js，同一命令还会运行 `test_frontend.cjs` 前端回归测试，
验证卡片复用、刷新合并、过期消息丢弃及错误时保留结果；不需要 npm 依赖。
未安装 Node.js 时该项明确跳过。该测试使用简化 DOM，不替代浏览器视觉验收。
测试使用临时目录和模拟网络/Git，不修改项目数据库，不运行 Agent，不上传代码。

CLI 是否找到的展示结果共用 5 分钟缓存，重启立即刷新；任务执行仍实时查找 CLI。
缓存状态测试使用可控时钟和任务调度，文件探测测试的临时目录位于项目内，
并严格验证清理成功，不忽略权限错误。

