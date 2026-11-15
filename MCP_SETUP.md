# Codex、DeepSeek Harness 与 Grok 接入说明

## Grok Build 接入补充（2026-11-01）

工作台调用 Grok CLI 与 MCP 消息桥是两条链路；CLI存在不证明桥接已登记。
Grok 启动命令可用 `GROK_CLI` 指定，否则检查PATH和用户目录 `.grok/bin/grok.exe`。
普通调用使用auto，接受非交互分类器拒绝工具、不等待人工批准且不保证工具执行；普通与最终均禁子Agent。专门final交付dontAsk并拒绝全部工具；owner自行收口不自动套用该限制，不宣称Windows操作系统级沙箱。

按[官方MCP说明](https://docs.x.ai/build/features/mcp-servers)，可在用户自己的 `~/.grok/config.toml` 中手动追加（不要覆盖已有配置）：

```toml
[mcp_servers.agent_bridge]
command = "C:/path/to/CoAgent/.venv/Scripts/python.exe"
args = ["C:/path/to/CoAgent/agent_bridge.py"]
env = { AGENT_BRIDGE_AGENT_ID = "grok", AGENT_BRIDGE_DB = "C:/path/to/CoAgent/agent_bridge.db" }
```

确认本机路径后由用户配置；本轮不写用户配置，不查询真实登记状态。
重新加载后，在普通协作阶段调用register_agent，核对返回agent_id为grok，再测试两端收发。
最终交付阶段拒绝工具，不应依赖该阶段进行MCP注册。
隔离测试证明通用桥接支持grok身份，不代替真实MCP连接验收。
具体修正及验证范围见[适配设计](docs/design/GROK_ADAPTATION_DESIGN.md)。

这个本地 MCP 服务让多个 Agent 通过同一份 SQLite 消息记录互相登记、发消息和读取对话。它只共享通过消息桥发送的内容，不会读取双方完整的私有聊天记录。

## 1. 安装依赖

在 PowerShell 进入本目录后运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 将服务接入 Codex

需要从Codex客户端使用消息桥时，先核对已安装CLI的mcp命令，再追加配置：

```powershell
codex mcp add agent-bridge `
  --env "AGENT_BRIDGE_AGENT_ID=codex" `
  --env "AGENT_BRIDGE_DB=C:/path/to/CoAgent\agent_bridge.db" `
  -- "C:/path/to/CoAgent\.venv\Scripts\python.exe" "C:/path/to/CoAgent\agent_bridge.py"
```

上面命令里的 Python 可执行文件应为：

```text
C:/path/to/CoAgent\.venv\Scripts\python.exe
```

添加后重启 Codex 客户端，让它重新载入 MCP 配置。

## 3. 将服务接入 DeepSeek Harness

需要桌面profile接入桥时，安装 `@deepseek-ai/dsh-mcp-client` 并追加配置，以下为位置示例：

```text
~/.dsh\profiles\desktop\cordis.patch.yml
```

配置条目：

```yaml
- insert:
    - id: mcp-agent-bridge
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: agent_bridge
        transport: stdio
        command: "C:/path/to/CoAgent\\.venv\\Scripts\\python.exe"
        args:
          - "C:/path/to/CoAgent\\agent_bridge.py"
        env:
          AGENT_BRIDGE_AGENT_ID: deepseek-harness
          AGENT_BRIDGE_DB: "C:/path/to/CoAgent\\agent_bridge.db"
        cwd: "C:/path/to/CoAgent"
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

## 5. 启动统一聊天界面并与三端对话

网页提供已启用CLI入口，也可读取桥消息；无需先登记MCP。工作台会把用户消息和回复保存在同一数据库的 `workbench_messages` 表，并为每个工作台对话、每个 Agent 单独续接会话。原有的 `agents` 和 `messages` 桥接表不会被工作台改写。客户端里的其他普通聊天不会自动导入。

无点名或 @all 是广播，各已启用身份独立回答；第一个有效点名决定协作入口，完整原文交给该身份。多个点名不会自动分段或并行分派，同一job每次执行一个就绪任务。CLI路径优先级与权限详见 [README.md](README.md) 第4节。

向 DeepSeek Harness 发送时，工作台通过 headless CLI 启动一个无界面 DSH 运行，并用 `--session-id` 续接该对话。首次使用前，在 DeepSeek Harness 桌面应用菜单中选择 **Manage dsh Command… → Install**，然后重新打开 PowerShell，确认 `dsh --version` 能运行。若 CLI 未注册到 PATH，也可以在启动工作台前设置 `DSH_CLI` 为 dsh 命令的完整路径。

如果 `dsh` 指向 DeepSeek Harness 桌面版自带的 `dsh.cmd`，工作台会直接调用 Electron 包内的 DSH CLI，并设置 `ELECTRON_RUN_AS_NODE=1`，绕过桌面 host 启动层。非桌面版安装或无法识别桌面安装结构时，工作台仍使用配置的 `dsh` 命令。

工作台使用 DSH 的 `headless` profile，并为每条工作台对话单独保存 session ID。凭据来自DSH已保存认证；`dsh-headless-auth.patch.yml`仅提供插件开关，可由DSH_HEADLESS_PATCH覆盖。

`dsh-headless-auth.patch.yml` 会为 headless profile 重新启用 DSH 的凭据、授权和 DeepSeek 账户插件，以便它使用 DSH 已保存的登录信息。该配置覆盖层只应用于工作台启动的 headless 进程，不会改写桌面 profile 配置。headless 会话与桌面应用中的普通会话分开保存；桌面聊天记录不会自动导入。headless profile 也不会自动继承桌面 profile 的 MCP 插件配置。

在项目目录打开**普通 Windows PowerShell**，运行：

```powershell
.\.venv\Scripts\python.exe .\web_chat.py
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

实际权限取决于后端配置与工作台传入参数，详见根README第4节。

### 工作目录

页面上方的“当前对话的工作目录”可为所选对话指定一个已存在的绝对路径。默认目录是本项目目录。路径按对话保存；更改时如果 Agent 正在运行，工作台会拒绝更改。更改成功后，该对话的共享消息保留，Codex、DeepSeek Harness 与 Grok 会在新目录建立新的后端会话。

三端使用所选工作目录；权限由CLI配置与宿主启动参数共同决定。

页面每 3 秒刷新一次。思考中的回复下可停止该 Agent，发送按钮旁的按钮可停止所有正在运行的 Agent；关闭工作台会清理已跟踪运行，不保证所有外部派生进程已退出。按 `Ctrl+C` 停止服务。默认使用本目录中的 `agent_bridge.db`；如果 MCP 服务配置了其他 `AGENT_BRIDGE_DB` 路径，启动网页前也要在当前 PowerShell 设置同一个值：

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



## 当前说明与历史

entry-3000修正本机路径、双端标题、旧分段路由和权限概括。历史版本见Git。网页快速开始见 [README.md](README.md)，MCP是可选旁路。示例路径须替换，保留已有客户端配置；本轮未验证真实MCP登记与干净环境认证。