# Agent 遵守规范的业界措施调研

> 本文收集「让 Agent 在执行任务时自动遵守适用规范」的已有应对措施，仅供后续参考，**暂不选型、不推荐实施方案**。
> 由 Codex 与 DeepSeek 分别独立调研后合并去重整理；覆盖「规范送达 → 适用性判断 → 执行约束 → 结果检查 → 持续改进」五个环节。

## 原始目标

让 Agent 在执行任务时**自动遵守对应规范**（当前即 `agent-discussion-spec v1.0.0` 等协作规范），实现方式不限，参考业界已有做法。

## 类型标记

- **产品**＝已有产品机制（成熟、可开箱用）
- **应用**＝基于已有能力组织的应用设计（需自行实现）
- **研究**＝论文方法，不代表已是通用生产方案
- **社区**＝社区/第三方实现，仅作示例，非行业标准

---

## 一、规范送达（让 Agent 收到规范）

| 编号 | 措施／类型 | 原理与代表实现 | 主要局限 |
|---|---|---|---|
| 1 | 指令优先级／产品 | 用系统、开发者、用户等消息角色区分规则权威。[OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering) | 模型遵从机制，非工具权限屏障 |
| 2 | 统一版本化提示模板／应用 | 集中构造规则提示，随代码审核/测试/发布。[OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering) | 送达≠遵从；接线仍可能出错 |
| 3 | 少样本示范／应用 | 提示里给符合要求的输入输出示例。[OpenAI](https://developers.openai.com/api/docs/guides/prompting) | 示例可能带偏或与正文冲突 |
| 4 | 指令与外部数据分隔／应用 | 用章节/标签/消息边界区分规范与引用资料。[OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering) | 分隔符非安全隔离，不能单独防注入 |
| 5 | 原生项目指令文件／产品 | 自动发现全局/项目/目录指令，如 `AGENTS.md`。[Codex](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 受发现路径、覆盖关系、容量限制影响 |
| 6 | 每次调用刷新关键指令／应用 | 请求构造时显式提供当前规则，不假定旧指令继承。[OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering) | 增加输入量；继承语义因接口而异 |

## 二、适用性判断（挑出适用条款）

| 编号 | 措施／类型 | 原理与代表实现 | 主要局限 |
|---|---|---|---|
| 7 | 按文件路径匹配规则／产品 | 用 glob 等条件自动附加专项规则。[Cursor Rules](https://cursor.com/docs/rules) | 文件匹配≠任务语义匹配 |
| 8 | 按描述选择规则／产品 | 展示规则说明，模型判断相关性后加载。[Cursor Rules](https://cursor.com/docs/rules) | 可能漏选/误选，依赖描述质量 |
| 9 | 规范库检索／应用 | 检索相关条款补充上下文。[OpenAI 检索](https://developers.openai.com/api/docs/guides/retrieval) | 漏召回、取旧版、丢例外；非现成合规 |
| 10 | Skills 渐进加载／产品 | 先发现名称描述，按需读流程/脚本/模板。[Agent Skills](https://agentskills.io/specification) | 是否选中、执行是否正确仍需检查 |
| 11 | 按角色预加载技能／产品 | 子 Agent 启动时直接注入指定技能。[Claude 子 Agent](https://code.claude.com/docs/en/sub-agents#preload-skills-into-subagents) | 预加载可能过多；配置需维护 |

## 三、执行约束（阻止违规动作）

| 编号 | 措施／类型 | 原理与代表实现 | 主要局限 |
|---|---|---|---|
| 12 | 代码工作流与阶段关卡／产品+应用 | 代码规定步骤和转移条件，模型在受控节点工作。[LangGraph](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | 只约束已编码流程；关卡条件可能薄弱 |
| 13 | 角色专门化与工具收窄／产品+应用 | 不同 Agent 独立职责、提示和可用工具。[Claude Subagents](https://code.claude.com/docs/en/sub-agents) | 角色提示仍是软约束；交接有成本 |
| 14 | 结构化输出 + Schema 校验／产品+应用 | 约束输出到 JSON schema，接收端再校验。[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Claude structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs) | 格式对≠内容对；只查结构不查语义 |
| 15 | 工具参数契约／产品+应用 | 操作表达成有明确参数结构的工具调用，执行端核验。[OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs) | 参数类型对≠有权限/语义合理 |
| 16 | 工具网关／策略引擎／产品+应用 | 工具调用先过网关按策略 allow/deny/approve。[agent-mcp-gateway](https://pypi.org/project/agent-mcp-gateway/)（社区）、AWS Bedrock AgentCore policy | 需维护策略；覆盖不全等于没覆盖 |
| 17 | 权限沙箱／权限模式／产品 | OS 容器或产品权限模式限制文件/网络/命令。[Claude Code permission modes](https://code.claude.com/docs/en/permission-modes)、[agent-sdk permissions](https://code.claude.com/docs/en/agent-sdk/permissions) | 粒度粗；资源边界非语义规范 |
| 18 | Hooks／产品 | 工具调用前/后插钩子，PreToolUse 可返回「阻止」。[Claude Code hooks](https://code.claude.com/docs/en/hooks) | 要写钩子代码，需覆盖所有入口；正文未全文核实 |
| 19 | 审批／Human-in-the-loop／产品+应用 | 敏感操作前暂停等人工批准。[LangGraph interrupts](https://docs.langchain.com/oss/javascript/langchain/human-in-the-loop) | 中断自动化、成本高；审批人判断也可能错 |
| 20 | 策略即代码（OPA/Rego）／应用 | 用声明式策略语言写可评估规则，调用前查询。[OPA+MCP](https://www.npmjs.com/package/@orygn/opa-mcp)（社区） | 学习/维护成本；策略引擎自身要治理 |
| 21 | Guardrails 框架／产品+研究 | 可编程 rails/护栏约束输入输出与对话流。[NVIDIA NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview) | 侧重安全/合规，泛化到业务规范需定制 |
| 22 | 输入/输出扫描器／社区 | 对 prompt 和输出做规则/模型扫描（PII、注入、越界）。[llm-guard](https://github.com/marirs/llm-guard-rs) | 规则扫描会漏检；扫描≠预防 |
| 23 | 沙箱执行隔离／产品 | 代码执行放容器/微 VM 隔离宿主。[E2B/Firecracker/gVisor](https://github.com/mchittineni/ultimate-ai-engineering-guide/blob/main/ai-agents-and-mcp/how-do-sandbox-execution-environments-secure-code-interpreter-tools.md) | 资源开销大；只管执行环境不管业务语义 |
| 24 | 提示注入防护／产品 | 分隔符隔离 + 锁定模式，防低信任内容覆盖指令。[OpenAI Lockdown Mode](https://itbrief.co.nz/story/openai-unveils-lockdown-mode-to-counter-prompt-attacks) | 分隔符非安全边界；不能单独防所有注入 |
| 25 | MCP 授权／规范 | OAuth 资源服务器给工具访问发授权。[MCP Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index) | 传输层授权，不校验业务语义 |

## 四、结果检查（事后验证）

| 编号 | 措施／类型 | 原理与代表实现 | 主要局限 |
|---|---|---|---|
| 26 | 外置计划与验收清单／应用 | 计划文件记录步骤、边界、验收条件和证据。[Codex 流程示例](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex) | 文件不能阻止跳步；自报完成可能不准确 |
| 27 | 独立审核者／应用 | 生成与检查交给不同调用/Agent，按规范审查。[LangGraph](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | 审核者可能共享盲点 |
| 28 | 评估—修订循环／应用 | 检查者返回反馈，生成者修订至达标/预算结束。[Evaluator–optimizer](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | 增加成本，可能循环；评分标准可能失真 |
| 29 | 可执行验收反馈／应用 | 让测试/编译器/检查脚本提供客观结果再纠正。[Claude Code 最佳实践](https://code.claude.com/docs/en/best-practices) | 检查覆盖有限；检查器本身也可能错 |
| 30 | Self-Refine／研究 | 同模型生成初稿、反馈、修订，不额外训练。[论文](https://arxiv.org/abs/2303.17651) | 自评可能重复自身错误 |
| 31 | Reflexion／研究 | 反馈转反思文本存记忆，影响后续尝试。[论文](https://arxiv.org/abs/2303.11366) | 依赖反馈质量；不更新权重、无阻断 |
| 32 | 验证测试／CI 回归／应用 | agent 行为写成可重复用例进 CI。[kensa](https://pypi.org/project/kensa/)、[EvalForge](https://pypi.org/project/evalforge-agents/)（社区） | 覆盖有限；事后发现非事前预防 |
| 33 | 审计／可观测性／产品 | 记录调用轨迹、输入输出、决策依据。[LangSmith](https://docs.langchain.com/langsmith/evaluation)、[OpenTelemetry GenAI](https://github.com/lmolkova/semantic-conventions-genai) | 审计是事后，不阻止错误 |
| 34 | 评测／LLM-as-judge／应用+研究 | 用模型/人打分反馈迭代。[微软多 agent 评估](https://learn.microsoft.com/training/modules/aaai-design-evaluation-frameworks-multi-agent-azure/) | 评测器会误判；是反馈不是强制 |

## 五、持续改进（训练与记忆）

| 编号 | 措施／类型 | 原理与代表实现 | 主要局限 |
|---|---|---|---|
| 35 | 上下文压缩与状态保留／产品+应用 | 压缩历史支持长任务，管理后续所需信息。[OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction) | 压缩不证明规范完整保留 |
| 36 | 清理上下文重新发起／产品+应用 | 多次纠偏无效时，用干净上下文+修订任务重来。[Claude Code 最佳实践](https://code.claude.com/docs/en/best-practices) | 可能丢有效历史 |
| 37 | 持久化反馈记忆／产品 | 保存用户纠正与项目经验，后续会话加载。[Claude Code Auto Memory](https://code.claude.com/docs/en/memory) | 记忆可能过时/错误 |
| 38 | 规则与记忆审计／产品+应用 | 检查过时内容、冲突、失效引用。[Claude 记忆管理](https://code.claude.com/docs/en/memory) | 审计可能漏检；发现问题≠已修复 |
| 39 | 监督微调 SFT／训练 | 用符合要求的样本训练行为模式。[OpenAI SFT](https://developers.openai.com/api/docs/guides/supervised-fine-tuning) | 需数据/权限/评估；规则变化需维护 |
| 40 | 偏好优化 DPO／训练 | 用偏好/不偏好输出训练行为偏好。[OpenAI DPO](https://developers.openai.com/api/docs/guides/direct-preference-optimization) | 偏好标签可能不一致 |
| 41 | 奖励驱动微调 RFT／训练 | 评分器评价训练输出，引导提高得分。[OpenAI RFT](https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning) | 依赖可评分目标，存在奖励投机 |
| 42 | Constitutional AI／研究 | 原则清单指导自我批评、修订、AI 反馈训练。[论文](https://arxiv.org/abs/2212.08073) | 原研究聚焦无害性，迁移需验证 |
| 43 | 指令层级训练／研究 | 训练模型优先遵循高权威指令。[论文](https://arxiv.org/abs/2404.13208) | 需训练支持，不能仅靠改提示获得 |

---

## 关键区分（供后续选型时参考）

1. **「送达规范」≠「遵守规范」**：前四类（指令、规则文件、条件加载、技能）只保证规范进入上下文，不保证模型照做。
2. **「确定性控制」vs「概率引导」**：工具网关、权限沙箱、Hooks、状态机、结构化输出、测试、审批是**确定性**（能拦/能验）；提示注入、技能、检索、LLM-as-judge 是**概率引导**（只劝告）。
3. **「审计/评测」是事后**，不阻止错误——不能把「有日志/有打分」当「已预防」。
4. **「格式正确」≠「内容正确」**：结构化输出只保证结构，不保证语义。

## 资料局限

- 未实测任何方案的遵从率、行业采用率或跨方案对比数据。
- Claude Code hooks / permission-modes 部分细节未读正文全文，靠搜索摘要交叉确认，实施前需核实。
- `agent-mcp-gateway`、`kensa`、`EvalForge`、`opa-mcp`、`llm-guard` 为社区/第三方实现，仅作示例，非行业标准。
- 研究条目（Self-Refine、Reflexion、Constitutional AI、指令层级训练）核实到论文摘要，未逐篇全文复核实验。
- 本文仅收集措施，**未选择本项目方案、未给出实施路线图**。
