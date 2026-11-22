# Grok 工作台适配修正

文档状态：已决定，用户在 entry-2093 授权修复；当前实施状态：entry-2105 正常文件访问恢复，R1旧断言已修正，回归无失败，Grok已独立复查通过并关闭R1；R2/R3及已核对P2维持关闭。历史失败记录保留如下；不代表已合入或真实CLI验收。
适用：agent-discussion-spec 1.2.0、design-decision-record-spec 1.1.0、code-review-spec 1.0.0（specs/）。
关联：[失败接收](FAILURE_VISIBILITY_DESIGN.md)、[UI](UI_SIMPLIFICATION_DESIGN.md)。

## 范围和依据

修复 entry-2086 的 G1–G7、D1/D2、K1 及测试缺口，不修改真实数据库、凭据或用户配置，不提交或合入。
作者 Codex，reviewer Grok。基线 feat/grok-agent / 0c8b56c；既有 Git 服务文档改动保留。

官方依据：[CLI](https://docs.x.ai/build/cli/reference)、[输出](https://docs.x.ai/build/cli/headless-scripting)、[权限](https://docs.x.ai/build/features/permissions)、[沙箱](https://docs.x.ai/build/features/sandbox)、[MCP](https://docs.x.ai/build/features/mcp-servers)。本机1.0.70帮助确认参数存在。官方页面未给完整 streaming-json 事件schema；不把fixture当官方契约。

## 选择与取舍

- 普通调用保留 auto：接受非交互分类器拒绝工具，不等待人工批准，也不保证工具执行；取消 always-approve。普通与最终均禁子Agent，协作由工作台调度；最终另加 dontAsk + deny '*' 和禁Web工具。不宣称Windows OS沙箱或hooks隔离；实际权限仍待验收。
- 输出接受单一非空ID与明确的 end_turn、EndTurn：安装包14-headless-mode.md使用前者，README.md使用后者；不泛化大小写。usage上的tool_use不参与end校验。异常、未知结束仍拒绝；真实输出尚未采样。
- 新ID只在正常结束且无错误、回复非空后保存；续接必须返回相同ID。错误候选只保留在本次结果供失败诊断，不写可续接表；不自动重试。
- 页面发送其预览的广播名单，后端只允许原路由名单的非空子集，且单Agent定向不得改目标；未传名单的旧API保持原语义。服务端仍即时检查可执行条件。
- 明确传名单时Grok不可用则整条请求503，提示刷新；不静默减少收件人。旧API未传名单仍按既有行为剔除不可用Grok，保留兼容性。
- legacy成功仍标unverified：已检查本次输出与ID，但未验证会话配置命名空间或真实续接。保留此保守状态并测试，不能提升为verified。
- 执行按registry.adapter映射，未知拒绝；展示未知名称保留原ID，头像样式保持原状。
- 历史grok-headless迁移归一到grok，保留源候选与needs_validation。只修迁移规则，不自动重写已有v2库。
- MCP与工作台调用分开：补显式agent身份及DB路径配置示例，隔离数据库测试桥接注册收发；不强制所有CLI调用都依赖MCP登记。
- 未知非JSON诊断做长度限制和脱敏保留，不因合法警告直接失败；未知JSON事件允许忽略，结束证据仍必须满足。

## 验收与风险

临时数据库+受控进程覆盖worker、会话与取消收尾；前端执行实际PAGE脚本；迁移测试保留原候选；运行相关回归、语法与diff检查。
模型请求、用户MCP、有效Windows权限及新版本输出均不在模拟验证结论内。失败保留既有终态/退出确认，不自动回退文件。

## 实际实施与验证（2026-11-01 +08:00）

- 新增grok_adapter.py集中处理命令、保守输出校验、诊断脱敏和收件人约束；web_chat.py接入，按registry.adapter分派，未知标签保留原ID。执行快照记录实际审批参数。
- G3新增绑定延后至校验通过并在取消锁内保存；配置解析器启用时也用相同输出检查影响normal_completion。未自动重放失败操作。
- database_schema.py归一化历史Grok别名，隔离测试证明两个源候选保留在同一needs_validation域，不改当前真实库。
- MCP_SETUP.md追加手动配置示例；临时DB验证grok注册发送；不写用户配置，不证明真实MCP连通。
- test_grok_adaptation.py覆盖正常/截断/冲突/未知结束原因、脱敏、权限参数、收件人验证、实际worker、续接不匹配、取消、超时、退出未知、协作三角色及重复接收、桥接与迁移；同步前端、缓存、direct-answer测试。在用smoke和version-only脚本加入Grok；历史专项探针暂不扩展。
- Python AST检查、PAGE JavaScript语法检查、git diff --check通过。专项运行92项通过（随后补两条退出/会话边界，已包含于最终全套）。
- 最终 `python -B -m unittest discover`：187项，185通过、1失败、1跳过，26.918秒；失败为旧test_grok_agent.py:33仍要求always-approve。此前另一条解析错误断言已兼容，最终不再失败。跳过为需显式启用的真实CLI用例。失败路径日志为故意注入，不作为通过证据。
- test_grok_agent.py直接读取被拒，apply_patch实际写入也失败；未删除、改权限或用其他通道覆盖。待有正常写权限后，把旧命令等值断言改为auto策略与最终dontAsk+deny全部工具断言，不恢复旧的全批准行为来迎合测试。
- 本轮无commit/push/merge。Windows有效工具拒绝、hooks副作用隔离、真实事件schema和MCP仍未验收；end_turn仅为保守接受集合。K2头像纯样式保持原状，身份标签已修。
- 改动超过400行建议目标主要来自完整隔离测试；按权限/解析/会话/迁移共同边界保留单一修复单元，不省略失败用例。真实效果及受保护旧测试未通过，不批准合入。

## Grok检视后修订（2026-11-01）

- R1：本轮正常Get-Content仍报UnauthorizedAccessException，未尝试改权限、删除重建或其他通道覆盖；旧断言未改，阻塞保留。
- R2：按安装文档明确兼容end_turn/EndTurn；新增异常原因、usage.tool_use、成功保存与失败不保存边界测试。
- R3：保留auto受限策略，明确非交互工具可被拒绝、无人工等待、不保证执行；同步MCP与能力说明，测试拒绝诊断脱敏可见及不保存绑定。
- P2：明确名单遇Grok不可用返回503提示刷新，旧请求未传名单继续兼容；HTTP测试验证两条路径在访问DB前分别进入Grok拒绝或剩余Codex可用性检查。前端实际刷新后检查禁用与恢复；子Agent、头像、legacy验证状态说明已对齐。
- 本轮专项：`python -B -m unittest test_grok_adaptation test_capabilities.HttpTests.test_explicit_broadcast_cannot_silently_lose_grok test_capabilities.FrontendTests`，21项通过。完整`python -B -m unittest discover`为192项，190通过、1失败、1跳过，24.886秒；唯一失败仍为test_grok_agent.py:33旧命令断言。九个Python文件AST及PAGE JavaScript语法检查通过，git diff --check通过。未调用真实模型、凭据或生产库。
- R1未满足；R2/R3及P2仅完成作者修订，待Grok复查，不自行关闭检视意见。真实CLI权限、hooks、事件采样及MCP验收缺口继续保留。

## R1访问恢复与修订（entry-2105）

- 用户调整权限后，正常Get-Content读取及apply_patch写入均成功；本轮未改ACL或绕过限制。仅修改test_grok_agent.py的命令断言，生产策略不变：普通auto且无always-approve，最终dontAsk与deny '*'、禁子Agent和Web，保留resume参数测试。
- 相关专项25项全部通过，耗时5.271秒，退出码0。完整`python -B -m unittest discover`：192项，191通过、1跳过、0失败，25.517秒，退出码0。跳过仍为需显式启用的真实CLI用例；隔离测试的故障注入日志不表示测试失败。
- `git diff --check`退出码0；仅有Git行尾转换提示。R2/R3及前轮P2已获Grok关闭；R1待本轮非作者复查。真实CLI权限、hooks、事件采样和MCP未验收，未commit/push/merge。

- 非作者复查结果：Grok核对14文件快照6789dba03ce686e48d59a53c07cb2d472357f7957c30064c52b967745b4a2d3f，关闭R1；专项25项通过，完整192项中191通过、1跳过、0失败（24.572秒），均退出码0。负责人接受关闭结论，已识别代码与隔离测试修正审核完成。此处及文首仅同步review状态，未再改变受检代码；真实CLI与MCP缺口仍保留。

## entry-3917 测试分支权限

用户确认 `feature/GitDiscussion` 专用于测试，并授权在普通调用使用 `dontAsk`，显式允许 `Bash(git *)`、`Read` 和 `Grep`。此前 Codex 与 Grok 建议使用一次性窄规则，因为 Git 前缀规则也可能批准推送。用户接受测试分支上的这一取舍，正式方案后续收紧。本次运行验收限于本地隔离探针。

适配器及现有测试已更新，23 项单元测试通过，覆盖普通新建、续接及最终交付参数。工作台加载更新后，由 Grok 独立验证本地暂存、提交、结束事件及清理。

## entry-4095 工作区文件操作评估

用户要求 Codex、DeepSeek Harness 和 Grok 的普通任务都能在获准工作区创建、编辑和删除文件。验收通过工作台正常入口进行，使用各端独立的临时文件，编辑后读回，删除后确认无残留。既有 Git 授权与最终交付的工具边界仍是设计约束。

Grok 1.0.77 随附的《Headless mode》仅在 daemon 路径列出专用 `Delete`，并说明它与编辑工具的可用性联动。当前工作台启动命令未指定 daemon，现场未检测到正在运行的 Grok leader。该工具是否出现在实际 Windows 工作台调用中，以及是否匹配工作区路径规则，仍待运行取证。现有 PowerShell 删除记录仅证明当次操作成功。随附的《Sandbox》未提供 Windows 文件系统隔离依据。

优先验证原生 `Delete` 的发现与路径限制。如果实际调用缺少该能力，候选方案是由工作台提供绑定到当前已批准根目录的专用文件接口。Grok CLI 支持通过项目或用户配置注册 MCP 服务，当前启动命令没有按次注入 MCP 配置的参数。该候选需先验证注册与加载方式、续接时的根目录绑定、以及 final 不开放写入工具。当前阶段未选定实现，也未更新启动参数断言。

### 已选实现

普通调用使用临时 Grok agent 定义注册本地 stdio MCP 文件工具，由工作台把已获准的执行目录作为服务启动参数传入。工具参数只接受相对路径，拒绝越界路径、重解析点和多重硬链接。最终交付不注册此工具。文件操作独立授权，Git 授权继续遵循 entry-3917 的测试仓库和分支范围。选择依据为本次工作台普通调用未暴露原生 Delete。

实现已接入工作台启动路径。两组 Grok 单元测试共 23 项通过，独立 stdio 探针完成创建、编辑读回、删除和清理，并确认拒绝已存在文件的重复创建以及越界、绝对路径、Git 元数据和备用数据流路径。真实 Grok 新建、续接和 final 的工具加载与执行行为仍待验收。路径校验覆盖可见重解析点，尚未验证并发目录替换时的隔离。

### 独立检视返修

Windows 文件服务打开工作台批准的根目录，核对句柄上的规范路径，再逐段打开其下的目录。操作期间持有目录句柄并禁止共享删除。目标以父目录句柄相对打开，检查重解析属性及文件链接数，写入和删除均使用该目标句柄。单纯重复字符串路径检查仍有竞态。Windows 编辑持有独占写入句柄并保留原文件身份和权限，发生写入故障时可能留下部分内容。当前支持本地盘符工作区。

逐段拒绝保留设备名，JSON-RPC 输入解析失败后继续处理后续请求。文件工具保护 Git 内部的 `.git` 路径，工作树中的 `.gitignore`、`.gitmodules` 和 `.gitattributes` 按普通项目文件处理。Git 临时授权按每次调用启动时的仓库和分支判定。

保留 [MCP stdio 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio) 的换行分帧。Grok 检视会话中的空工具列表缺少启动配置与握手日志，实际新建、续接与 final 的工具授权由工作台验收。

## 单次 Grok 工具发现诊断

entry-4095 的用户更新 `15b180f5-cf95-4cd4-a55f-50066058a37f` 授权一次工具发现诊断，`bdfe0170-f58b-4cb7-9eef-7e4fc8128287` 授权实现入口及必要的受控重载。验收要求默认关闭，绑定一条普通新建 Grok 运行，并检查成功和失败后的临时日志清理。

采用服务进程环境中的唯一新对话 ID 预选请求，在普通消息事务中核对、以助手消息 ID 绑定，再在 Grok 启动前核对新建模式和 final 边界。脱敏摘要写入运行快照。全局调试会扩大采集范围，独立 CLI 检查覆盖另一条启动路径。本方案使用本次运行专属的临时目录。文件服务记录固定阶段标记，区分服务响应与客户端最终工具可见性。无法识别的客户端事件保持未知。

启用时给工作台服务进程设置 `WORKBENCH_GROK_DIAGNOSTIC_CONVERSATION`，值为新生成且未使用的对话 ID。服务须加载本节对应代码和环境设置。以本机回环地址通过普通 `POST /api/messages` 提交一次请求，传相同的 `conversation_id`、无命名提及的正文和 `recipients: ["grok"]`。按响应中的 Grok 助手消息 ID 查询本地 `execution_runs.source_message_id`，读取同一行的 `run_id` 和 `config_snapshot.grok_diagnostic`。用完后移除服务环境变量。受控重载前须确认无活动运行。

摘要涵盖诊断实现版本、Grok 可执行文件指纹、参数存在性、进程结果及文件服务阶段。客户端阶段、构建号和最终工具名的覆盖依赖该构建的调试日志格式，缺失即未知。当前已用模拟输出验证匹配及脱敏，并通过模拟成功与超时验证原始日志清理。文件删除失败时由临时目录清理继续处理，摘要标记待目录清理。进程被强制中止或操作系统拒绝清理时仍需核对残留。真实工作台运行待单独验收。

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-21 18:45:51 +08:00 | entry-4095／单次诊断 | 缺口→普通新建入口与本地脱敏摘要 | 用户两次授权及作者专项验证 | 用户授权，Codex 实施 | 默认关闭，实际客户端事件及受控重载待现场验收 |
| 2026-11-21 18:09:52 +08:00 | entry-4095／独立检视返修 | 路径快照→Windows 句柄相对操作 | 两端检视、Windows 本机验证；时间源为本机时钟 | Codex | 文件边界及协议容错；真实工作台验收与独立复查待完成 |
| 2026-11-21 17:59:24 +08:00 | entry-4095／工作区文件接口 | 候选→接入启动路径 | 普通调用未暴露 Delete；单元测试和 stdio 探针通过；时间源为本机时钟 | Codex | 现场验收与独立检视待完成 |
| 2026-11-21 17:54:37 +08:00 | entry-4095／文件操作评估 | 三端文件操作需求→记录 Grok 候选路径及验收条件 | 用户补充、Grok 1.0.77 本机手册及 leader 列表；时间源为本机时钟 | 用户提出；Codex 取证 | 实现及三端运行验收待完成 |
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-01（原记录精度） | 原历史阶段 | 沿用原记录 | - 2026-11-01 +08:00：由识别清单进入获授权修复；记录实施前选择和未验证项。 | 原记录所列来源 | 原范围及验证边界保留 |
