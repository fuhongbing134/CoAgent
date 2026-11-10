# 后续需求计划（Backlog）

> 本文件记录「尚未实现、但无功能阻断、暂缓到以后实现」的需求，用于将来判断何时值得做、避免遗忘。
> spec 专注约束（见 `specs/AGENT_DISCUSSION_SPEC.md`），本 backlog 专注未实现功能、暂缓理由与重新启动条件。

## 字段定义

**必填**：

- **需求**：一句话名称 + 简短描述。
- **状态**：`已确认但暂缓` / `尚待决定` / `可选`（不能把「可选/待决定」写成「已承诺」）。
- **暂缓影响**：不做的后果，分级为 `便利性` / `功能受限` / `正确性问题`。
- **优先级**：高 / 中 / 低（仅相对排序，不代表现在做）。
- **启动条件**：什么情况触发重新评估。
- **关联**：指向相关 spec / 日志 / issue。

**建议填**：

- **价值**：为什么值得做。
- **当前替代方式**：现在怎么手工/绕过。
- **验收标准**：做完后怎么算「完成」。

---

## 条目

### 1. 裁决后负责人优先调度（方案 A 可靠闭环）

- **需求**：用户对僵持问题裁决后，保证「用户回答落库 → 负责人先读裁决并决定后续 → 才继续依赖该裁决的任务」，而不是先按旧指令执行 ready 任务。
- **状态**：已确认但暂缓
- **暂缓影响**：正确性问题（可能先执行旧任务、再处理用户裁决）
- **优先级**：高
- **价值**：确保用户裁决真正生效，不被旧任务抢先。
- **当前替代方式**：挂起时确保无 ready 任务，手动避免旧任务先跑。
- **启动条件**：开始实现方案 A 的可靠闭环时。
- **验收标准**：用户回答落库后，负责人先读裁决再继续依赖裁决的任务。
- **关联**：`specs/AGENT_DISCUSSION_SPEC.md` §10

### 2. 协作者直接互邀

- **需求**：协作者可直接发起 `request_collaboration` 邀请其他 Agent 讨论，无需先经负责人转交。
- **状态**：已确认但暂缓
- **暂缓影响**：功能受限（仍需负责人转交，未实现用户期望的讨论方式）
- **优先级**：中
- **价值**：实现「任何 Agent 可发起讨论」的目标。
- **当前替代方式**：协作者把议题/对象/原因交给负责人安排。
- **启动条件**：需要支持协作者自由互邀时。
- **验收标准**：协作者可直接发起邀请；需改现有授权/调度机制（当前仅 entry/owner 可邀请）。
- **关联**：`specs/AGENT_DISCUSSION_SPEC.md` §2

### 3. 预算不足自动挂起（B1 兜底）

- **需求**：预算不足时不直接收尾取消待办，而是保存待办、生成裁决问题并等待用户（补预算或结束）。
- **状态**：尚待决定（可选）
- **暂缓影响**：现有限制（预算不足可能收尾并取消待办；方案 A 提前等待只能降低发生概率）
- **优先级**：中（可选）
- **价值**：满足「即使额度不足也绝不自动收尾、保留原任务等裁决」。
- **当前替代方式**：方案 A 提前 `waiting_user` + 提前预留预算。
- **启动条件**：用户明确要求「额度不足也不自动收尾」时。
- **验收标准**：预算不足分支分流保留待办、生成裁决问题并等待，用户加预算或结束，且不丢任务、不无限自动重试。
- **关联**：`specs/AGENT_DISCUSSION_SPEC.md` §8、§10

### 4. 专用「追加 N 次」按钮

- **需求**：提供便捷的预算追加操作（后端原子追加）。
- **状态**：已确认但暂缓
- **暂缓影响**：便利性（已有「调整上限」可替代）
- **优先级**：低
- **价值**：操作更直观。
- **当前替代方式**：手动 `set_limit` 调整上限（如 18/20 → 18/40）。
- **启动条件**：需要更便捷的预算追加时。
- **验收标准**：后端事务内读上限相加、幂等、授权、100 次边界；语义区分「追加 N 次」（上限+N）与「补足剩余至 N 次」，两者不同名。
- **关联**：`specs/AGENT_DISCUSSION_SPEC.md` §8

### 5. 入口自动注入（规范治理）

- **需求**：工作台后台统一注入规范入口与索引（复用 `web_chat.py` 的 `build_agent_prompt()`，注入 `AGENTS.md` 入口 + `specs/README.md` 索引，正文按需读，不注入全部正文）。
- **状态**：已完成
- **暂缓影响**：无（已完成）
- **优先级**：高（最高，修完当前 bug 后第一个做）
- **价值**：实现「用户无感 + 所有 Agent 可靠收到入口」。
- **当前替代方式**：靠 Agent 自觉读 `AGENTS.md`/specs（不可靠，本次会话因 `AGENTS.md` 在上下文而读到，其他路径未验证）。
- **启动条件**：P0-2 并发会话冲突相关 bug 修复完成后。
- **验收标准**：`build_agent_prompt()` 统一注入 `AGENTS.md` + `specs/README.md`，覆盖普通聊天/entry/owner/collaborator/final/澄清重规划/CLI 恢复；按实际工作目录读取（`resolve_working_directory()`）；每次调用刷新、不写入用户原文、不累积共享历史、不静默截断或忽略失败；保留角色边界（final 只能汇总）；回归通过（两后端、新建/恢复、各角色含入口、用户原文与协议提示完整、final 限制不破坏）。
- **完成说明**：注入已实现并验证——build_agent_prompt 统一注入 AGENTS.md 入口 + specs/README.md 索引，按实际工作目录(working_directory)读取，失败抛错；35 项既有测试 + 隔离检查通过；场景1(普通问答)与场景2(协作讨论)真实后端验证了入口送达、判适用、读正文、handoff.evidence 留痕。剩余待补：真实后端两后端新建/恢复、各角色逐入口、final 限制的完整回归。
- **关联**：`AGENTS.md`、`specs/README.md`、`web_chat.py` `build_agent_prompt()`（`main.py` 独立 Codex 入口暂不覆盖）

### 6. 同一 agent 并发多任务 / sub-agent 分流

- **需求**：同一 agent 正在执行任务时，用户再次派活，自动起 sub-agent（独立执行域/会话）并发做第二个任务，而不是拒绝。
- **状态**：尚待决定（可选）
- **暂缓影响**：功能受限（当前只能拒绝 + 提示「正在忙」，等待任务 A 完成）
- **优先级**：中
- **价值**：agent 忙时也能接新任务，提升并发体验。
- **当前替代方式**：任务 A 已落地——提示「该 agent 正在忙另一个任务，请等它完成后再试」。
- **启动条件**：用户确认需要「agent 忙时并发接单」能力。
- **验收标准**：待设计；需先解决执行域模型（当前一个 `(agent, conversation)` 同时只能一个 run），如何支持多个并发 run + 各自独立 session，且不破坏 P0-2 的「防并发写坏」。
- **关联**：P0-2 原始场景（并发会话冲突）

---

### 7. 前端测试 DOM mock 补齐 Node.before

当前处置（2026-11-10，entry-2647）。原触发问题已随 UI 简化移除 `messagesNode.before()` 调用而消除，当前 `collaboration_ui.js` 使用 `taskHost.append(collaborationRoot)`。用户报告的后续专项 10 项及全套 167 项回归通过（1 跳过），依据见[UI 设计第 8 节](docs/UI_SIMPLIFICATION_DESIGN.md#8-实施结果风险与剩余缺口)。本次仅核对文档与源码，未重跑测试。原补齐 mock 的独立修复方案已由调用移除方案取代，以下暂缓状态、失败证据及验收要求保留为登记时历史。

- **需求**：修复 `test_frontend.cjs` 的 DOM mock 缺少 `before()`，导致 `collaboration_ui.js:5` 的 `messagesNode.before(collaborationRoot)` 抛错、`test_capabilities.FrontendTests.test_polling_regressions` 失败的问题。
- **状态**：已确认但暂缓（独立修复；暂缓对合入门槛的影响尚待确认）
- **暂缓影响**：正确性问题（前端回归测试无法通过，轮询断言被初始化异常阻断；不据此认定真实浏览器也缺该方法）
- **优先级**：中
- **当前证据与处置**：用户 2026-10-29 全套运行 167 项：165 通过、1 失败、1 跳过，唯一失败为此项。DeepSeek 判为既有独立问题、本次未改前端，建议不阻塞失败可见性；未在旧基线上重跑确认历史起点，登记待办不等于用户已批准豁免失败检查。
- **启动条件**：安排独立前端测试修复时；如合入前需消除该失败，则优先处理，或由负责人提交明确门槛处置供用户裁决。
- **验收标准**：补齐符合调用语义的 DOM mock，保持既有轮询断言有效；`node test_frontend.cjs` 与 `python -m unittest test_capabilities` 通过，并复核全套结果，不以删除断言或跳过用例制造通过。
- **关联**：[失败可见性设计 §8](docs/FAILURE_VISIBILITY_DESIGN.md#8-实施与验证结果)、`collaboration_ui.js`、`test_frontend.cjs`、`test_capabilities.py`；合入门槛见 `specs/CODE_REVIEW_SPEC.md` §10。

### 8. owner 等待或阻塞回复的空计划兼容

- **需求**：修复 owner 把空 `plan.tasks=[]` 当成省略 `plan` 时的协议中断；当前 owner 的 `waiting_user`/`blocked` 携带空计划会报 `tasks must be a nonempty list`，无法正常进入等待或阻塞阶段。
- **状态**：已确认但暂缓；**待修复，未实施**。用户要求先记录、稍后修复；收窄版 A+B 已讨论收敛，不代表已授权本轮实施。
- **暂缓影响**：正确性问题（子任务已 completed，但后续 owner 接收失败使 job failed）。卡片按 job 显示失败是正确的，不是把已完成子任务误标失败；已保存代码成果不会因此自动回滚。
- **优先级**：高
- **证据与根因**：历史样本 `entry-1969`、`entry-1963`、`entry-1957` 均记录过 owner 的 `waiting_user + plan.tasks=[]` 被拒；前两者已核查子任务 completed、owner call failed，job 原因为上述错误，第三者沿用此前诊断。直接原因是 DeepSeek owner 反复将空计划当作省略计划；`collaboration_protocol.py:120` 的 owner 解析校验及 `:80` 的归一化校验目前仅对 completed 允许空 tasks。blocked 为同一校验限制覆盖的状态，非上述样本已实际触发的状态。
- **当前替代方式**：owner 无计划更新时省略整个 `plan`；waiting_user 仍须提供 questions，blocked 仍须提供 blocked_reason，不以 completed 代替等待。
- **待修复方案（收窄版 A+B）**：仅兼容 owner 的 waiting_user/blocked 在计划结构、owner_id/final_responder_id 身份合法且与当前计划一致时携带空计划，视为「无计划更新」、保留旧任务；不能直接丢掉空 plan 而吞掉角色篡改。解析层与归一化层采用一致规则，接收事务内基于最新计划再查；提示明确无更新时优先省略 plan。不扩大 entry/collaborator/final/continue_work/request_collaboration 的现有兼容范围，保留 completed 空计划的既有规则。
- **启动条件**：用户决定启动修复后，先把收敛方案写入现有调度设计记录，再实施代码、独立 review 与回归；本条仅登记待办，不代替设计决策记录或验收。
- **验收标准**：从真实解析入口经服务到事务接收覆盖 waiting_user/blocked 空计划与省略计划等价，旧任务保留且不重执行；篡改角色仍拒绝，缺 questions/blocked_reason 仍拒绝；completed 兼容及其他角色、状态和 final_delivery 边界不退化。不自动重放或改写历史失败 job。
- **关联**：[调度计划接收设计](docs/COLLABORATION_DISPATCH_DESIGN.md)、`collaboration_protocol.py` 的 `validate_payload`/`normalize_owner_plan`、`collaboration_service.py` 的 `call`（先解析再归一化）、`collaboration_lifecycle.py` 的事务接收、`test_issue8.py`；与失败可见性验收分开跟踪。

### 9. owner 协作协议 status 与邀请不一致（混合计划误用 continue_work 夹带 @邀请）

- **需求**：修复 DSH owner 在混合计划（plan 同时含自任务与他人任务）时，status 误用 `continue_work` 却夹带末行 `@邀请`；按协议，含他人任务需授权他人时应为 `request_collaboration` + 末行 `@名称`，`continue_work` 仅用于自任务且不写邀请行。此前 DSH owner 已多次出现协作协议 JSON 问题（duplicate `task_id`、多余 `agent_id_note` 字段等），应统一记录并修复/预防。
- **状态**：已确认但暂缓（待修复，未实施）
- **暂缓影响**：正确性问题（调度器无法按 status 正确分发授权/邀请，任务卡住或角色边界被破坏）
- **优先级**：高
- **价值**：确保 owner 协作协议回复可被调度器正确解析分发。
- **当前替代方式**：owner 严格区分——`continue_work` 不写邀请行；含他人任务用 `request_collaboration` + 末行 `@名称`。
- **启动条件**：安排协作协议校验或 DSH 回复自检修复时。
- **验收标准**：owner 混合计划正确使用 `request_collaboration`、`continue_work` 不带邀请、调度器按 status 正确分发。
- **关联**：`AGENT_DISCUSSION_SPEC.md`、`collaboration_protocol.py`、`docs/COLLABORATION_DISPATCH_DESIGN.md`

> 注：P0-2 的后续实施项（实例锁、生产迁移、session 条件写回、工作区身份、统一 Adapter 等）由 `P0-2_VERIFY_LOG.md` 追踪，不在本 backlog。
