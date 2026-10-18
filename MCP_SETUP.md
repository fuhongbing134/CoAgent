# Codex 与 DeepSeek Harness 消息桥

这个本地 MCP 服务让多个 Agent 通过同一份 SQLite 消息记录互相登记、发消息和读取对话。它只共享通过消息桥发送的内容，不会读取双方完整的私有聊天记录。

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

