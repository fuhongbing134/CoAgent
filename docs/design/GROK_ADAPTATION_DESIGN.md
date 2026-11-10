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

## 变更历史

- 2026-11-01 +08:00：由识别清单进入获授权修复；记录实施前选择和未验证项。

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
