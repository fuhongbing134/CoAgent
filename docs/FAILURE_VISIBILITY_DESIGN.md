# 协作失败可见性设计

## 1. 文档状态与关联

- **文档状态：已决定；方案状态：已实现，针对性本地回归通过；全套仍有 1 失败、1 跳过，真实环境待验收**。entry-1891 收敛方向 A，entry-1899 细化接口与事务边界；entry-1957 经用户授权完成编码，后续修正回归问题并经 DeepSeek 复审。全套检查尚未通过，不据此宣称合入门槛满足。实施结果见第 8 节，已落实的接口细节与剩余风险见第 9 节。
- 适用规范：[agent-discussion-spec v1.2.0](../specs/AGENT_DISCUSSION_SPEC.md)、[design-decision-record-spec v1.1.0](../specs/DESIGN_DECISION_RECORD_SPEC.md)、[code-review-spec v1.0.0](../specs/CODE_REVIEW_SPEC.md)。
- 关联：[调度计划接收设计](COLLABORATION_DISPATCH_DESIGN.md)、[设计索引](README.md)、[web_chat.py](../web_chat.py)、[collaboration_service.py](../collaboration_service.py)、[collaboration_lifecycle.py](../collaboration_lifecycle.py)、[collaboration_actions.py](../collaboration_actions.py)、[collaboration_ui.js](../collaboration_ui.js)。
- 本文记录前序定位、决策及本轮实施结果；历史代码行号为定位时位置，后续按函数定位。本次补记修改本文及 BACKLOG，不执行历史 job 或真实后端。

## 2. 背景与目标

- 用户在 owner 追加阶段后只看到“没人回复”。entry-1871 因 `status/invitation conflict` 失败；entry-1879 两任务完成，但最终因 `questions required only for waiting_user` 收尾失败。增量派发成功与整个 job 成功必须分开。
- 已核查这两个 job 的聊天消息仍为 completed，无执行失败提示，lifecycle 却为 failed。CLI 返回成功与协议接收成功在展示上混淆，用户可能看到已承诺继续或成功的原回复，却不知道调度已经终止。
- 目标：本次调用的回复在解析、计划归一化及事务接收失败后，能展示失败及可追踪原因，并保留原回复；正常接收不遗留 pending，取消与执行域保护不退化。
- 消息 completed 表示该次调用已被接收，不代表整个 job 已完成；合法 continue_work、request_collaboration、waiting_user 等仍按各自生命周期推进。

## 3. 范围与约束

- 聚焦协作调用消息终态与协议接收结果一致性，覆盖解析、归一化、事务接收及错误展示的衔接；普通聊天、entry、final 必须做兼容回归。
- 不改变公共 `update_workbench_message()` 默认 pending 更新保护，不自动更改 Agent 的协议意图，不扩大授权，不自动重试或恢复历史任务。
- 本轮不预设修改布局、增加通知系统或重建调度器；协作卡片/API 可见性纳入验收，其运行时问题需证据决定后续修改范围。
- 设计阶段只修改对应设计文档与本次明确授权的设计索引；实施另获授权。保留既有退出确认、取消和其他终态保护。

## 4. 选定方向：A 的协作专用暂存与终态确认

1. 协作调用收到回复后暂存原文，消息保持待确认；独立表达“传输成功”与“协议接收成功”，不能从 pending 推断 CLI 未返回。
2. 消息完成确认须覆盖 **解析校验 → owner 计划归一化 → 事务接收**。非 owner 不增加归一化职责，沿用各角色限制；不能仅在解析通过后提前标 completed。
3. 对真实协议错误，保留原回复并追加失败原因，消息标 failed；非法计划不得部分写入或派发。专用终态提交须绑定 message/job/call，条件更新、幂等，不覆盖 cancelled 或其他已确立终态，不用迟到结果修改不相关调用。
4. `accept` 返回未接收不必然等于协议失败：重复结果、停止、用户更新使旧回复失效等，应按下表分类，不能统一误报 failed。
5. **执行域先安全收尾，消息后确认**：退出证据与运行结果不能依赖仍为 pending 的展示状态。保留退出未确认即阻断派发的规则；执行收尾失败不得被稍后的成功确认覆盖。需明确传输结果、退出确认、协议接收和展示状态之间的传递，不能直接复制 entry 的上下文开关。
6. 若需专用失败兜底写回，同样须绑定调用、幂等并保留取消保护，不放宽公共更新函数。接收结果与消息终态必须使用同一事务提交，不采用 accept 已提交后另开事务确认消息的方式。

### 4.1 接口细化

以下为已决定的接口契约，实施已完成、行为验证待执行；实际类型适配与内部扩展见第 8、9 节，不改变事务与保护边界。

| 接口 | 契约与责任 |
|---|---|
| 两个 backend worker | 显式返回 `WorkerResult(transport_state, text, process_result, session_validation)`。成功、超时、取消、异常各分支明确赋值，原文与传输状态来自执行结果，不从消息表反推；可将已有 status 明确为传输语义，不额外保留含义重复字段 |
| `structured_worker()` | 验证 backend 返回结构化结果；缺失结果按失败处理。worker 内部会捕获异常，不能以未抛异常推定成功，也不再查询展示状态构造传输结果 |
| `stage_collaboration_reply(message_id, job_id, call_id, text)` | 协作专用暂存：仅关联匹配且消息 pending 时存原文，保持 pending，不新增 schema 状态。须在 worker 写 completed 前进入此路径，不把 completed 改回 pending，不借用 entry 的 validating_entry 开关 |
| `finish_execution(message_id, *, outcome=None)` | 增加显式执行结果参数，协作路径必须传已确定结果。执行层结合传输、取消、运行错误、session 校验及真实退出证据判定；传输成功不等于退出已确认。普通聊天与 entry 可保留兼容行为 |
| `InvocationResult` | 携带传输结果、原文、dispatch_allowed 与准确 message_id；job/call 由当前调用上下文关联，禁止按最新消息猜测 |
| 内部接收结果 | 分类为 accepted / rejected / superseded / cancelled / duplicate，并携带原因；可保留 bool 兼容接口，不将 accept=False 一律视为协议拒绝。服务层接收前及事务内的旧回复失效分支都须覆盖 |
| `finalize_message(db, *, message_id, job_id, call_id, disposition, reason="")` | 使用接收层当前事务连接，不自行开事务或 commit。由工作台提供专用实现并注入，服务层不直接依赖界面模块。核对关联与允许状态，accepted→completed；真实 rejected→保留原文、追加一次原因、failed；cancelled 不覆盖取消，duplicate 幂等无操作，superseded 单独处理且不误报协议失败。实施增加内部 inspect 操作，只读返回当前消息状态，用于同事务取消检查，不增加接收分类、协议字段或数据库状态；具体规则见第 9 节 |

普通聊天与 entry 保持各自既有完成路径；final 属于协作调用，须经过新的接收确认路径。公共 update_workbench_message 默认规则保持不变。

### 4.2 数据流顺序

1. worker 返回明确传输结果与原文；协作原文暂存，消息保持 pending。
2. 安全收尾执行域，保留退出确认与 session 等保护；收尾失败阻断接收及后续派发，保留故障提示，不让稍后确认覆盖失败。
3. 服务层解析、归一化并进入事务接收；格式错误也进入对应拒绝记录路径，不绕过消息确认。
4. 同一事务内确认本次 call/lifecycle 接收结果与消息终态；消息写入失败使本次接收事务回滚，非法计划不得部分接收。调用完成不代表整个 job 完成。
5. 事务提交成功后才继续驱动；不得在提交失败时派发新增任务。

### 4.3 崩溃、存储与幂等边界

- 接收事务提交前崩溃：该事务全部回滚，不派发新增任务；此前已暂存的原文、已完成的执行域收尾不属于此次回滚范围。中断后的 pending 留待核查，不自动重跑 Agent。
- 接收事务提交后崩溃：消息终态与接收结果一起持久化，消除 accept 成功而 finalize 尚未提交的窗口。
- 重复回调按 job/call/message 及既有接收结果判定：相同结果无操作，冲突结果拒绝，原因不重复追加，不覆盖 cancelled 或其他已确立终态。
- 数据库不可写：停止派发并报告存储异常，不伪装成协议错误，不无限重试；数据库不可用时无法保证错误可靠落库。执行域释放失败仍受原退出确认保护，不能为展示成功绕过保护。

## 5. 决策理由与定位证据

- `web_chat.py` 的 DSH worker（定位时 :2100）先写 completed，`invoke_collaboration`（:187–193）随后校验协议并尝试写失败；公共更新函数（:1464–1467）的 `AND status='pending'` 使已完成消息无法改写。该保护本身仍有取消竞争等用途。
- `structured_worker`（:1483）从消息表构造 WorkerResult，`finish_execution`（:1527）也依赖消息状态判运行结果；因此只把 completed 改成 pending 会影响传输判断及执行收尾，需要解耦。
- `CollaborationService.call` 在 `_invoke` 返回后才做归一化和事务接收；前置解析无法覆盖接收层最新计划、角色与依赖校验失败。
- 方向 A 优先从完成确认时序解决问题，避免普遍允许成功终态被后续错误覆盖；这是当前取舍，不是已量化证明 A 改动最小或绝对更优。
- 协作卡片已有 failed 标签和 reason（web_chat.py:868–876），ActionStore.view 返回 lifecycle，API 与三秒轮询存在。初次定位时卡片不可见原因尚未核实；后续两个样本的 API 与用户页面观察已确认失败展示正常，位置问题与消息更新缺陷分开记录，见第 9 节。

## 6. 备选方案与未采纳原因

| 方案 | 取舍 |
|---|---|
| B：允许 failed 覆盖 completed | 可能减少时序改动，但公共放开会扩大写入权限。若改为专用写回，也需绑定 message/job/call、幂等及取消保护；仍有先显示成功再失败的窗口。保留为成本对照，未选为主方向 |
| 完成写入前增加前置解析 | 能拦部分格式错误，不能替代归一化与事务接收校验，单独不满足目标 |
| 另写失败消息 | 可辅助通知，但不能修复原消息成功状态，另有去重与关联成本；不单独采用 |

## 7. 验收标准与验证方式

以下是待执行的验收计划，不是已通过结果：

1. **错误路径**：格式错误、归一化错误、事务接收拒绝均展示失败和原因，保留原回复，不派发非法任务；错误可关联到准确调用。
2. **正常路径**：正常协作、普通聊天、entry、final 不退化、不永久遗留 pending；消息接收成功不冒充整个 job 成功。覆盖两后端，暂存回复不能使传输或运行结果误判失败。
3. **竞争与幂等**：取消竞争、重复回调、用户更新使旧回复失效时，不覆盖取消、不误报协议失败、不重复追加原因、不串写其他调用。
4. **退出与存储失败**：执行域释放失败、消息写入失败及接收后提交中断不绕过退出确认，不伪报成功；明确可持久化证据和剩余不一致窗口，不以无限重试掩盖问题。
5. **真实可见性**：实际页面轮询能呈现聊天失败和协作卡片原因，核对 API 数据、DOM 呈现与用户可见位置；如卡片仍不可见，先记录运行时断点再决定修改。

优先用临时数据库与受控响应验证错误和竞争路径；真实页面验证使用独立无副作用任务，不重放历史 job。

## 8. 实施与验证结果

- **已实现，针对性本地回归通过，完整验收待完成**：entry-1957 中 Codex 在 `dev/failure-visibility` 编码，基线 `21bfdd2a52e8507c93a5fbd775118082e2b67836`；修改 `web_chat.py`、`collaboration_service.py`、`collaboration_lifecycle.py`、`collaboration_actions.py`，初轮生产代码新增 173 行、删除 71 行，初轮未修改测试或数据库结构。该统计为初轮快照，不含后续回归修复及测试适配。
- 实现内容：两个 backend worker 显式返回 `WorkerResult`（保留 `status` 字段表达传输语义，并携带原文、process_result、session_validation）；`structured_worker` 校验结构化结果；`stage_collaboration_reply` 关联校验后暂存原文并保持 pending；协作路径向 `finish_execution` 传显式 outcome；`InvocationResult.message_id` 传递准确消息关联；`ReceptionResult` 分类 accepted/rejected/superseded/cancelled/duplicate，并保留 `accept` 的 bool 兼容入口；`finalize_message` 在接收层同一事务提交消息终态，覆盖普通协作 call 及澄清 replan 路径。
- 已执行检查：`python -m py_compile web_chat.py collaboration_service.py collaboration_lifecycle.py collaboration_actions.py` 与 `git diff --check` 通过；完整 diff 自查及 AST 比对确认公共 `update_workbench_message` 未改变。这些是语法、差异与静态自查证据，不是行为回归通过。
- 初次 DeepSeek review 为代码检视通过、未发现 P0，但验证证据不足。后续回归暴露并修正 stopped 检查顺序、旧 mock 返回 None 及 previous 分支抛错破坏幂等的问题；`test_direct_answer.py`、`test_execution_wiring.py` 的 mock 已适配 `WorkerResult`，DeepSeek 已复审这些修复。初次 review 不代替后续版本的验证，适用合入门槛为 code-review-spec §10。

以下结果均来自用户在普通 PowerShell 提供的运行日志，本轮仅整理，未重新运行；日志未附被测工作区的完整版本指纹。

| 命令 | 用户报告结果 | 验证边界 |
|---|---|---|
| `python -m unittest test_issue8 test_direct_answer` | 28 tests，OK，3.811s | 针对性调度及直接回答回归通过 |
| `python -m unittest test_execution_wiring` | 14 tests，OK，3.817s | previous 幂等及 mock 适配回归通过；日志中的 release fault 和退出未确认异常为受控失败路径，不等于测试失败 |
| `python -m unittest discover` | 167 tests，FAILED (failures=1, skipped=1)，22.351s；2026-10-29 19:41:11 开始 | **165 通过、1 失败、1 跳过**，全套未通过；不能记为「166 通过 + 1 跳过」 |

- 唯一失败是 `test_capabilities.FrontendTests.test_polling_regressions`：`collaboration_ui.js:5` 调用 `messagesNode.before(collaborationRoot)`，`test_frontend.cjs` 的 DOM mock 缺少该方法，报 `TypeError: messagesNode.before is not a function`。DeepSeek 判为前端既有独立不匹配、非失败可见性改动引入（本次未改前端）；本轮未在旧基线上重跑确认其历史起点。已登记 [BACKLOG 第 7 项](../BACKLOG.md)。跳过项的具体原因未在本轮材料中确认。
- **合入处置保留意见（Codex，2026-10-29 19:42:38 +08:00）**：DeepSeek 建议该独立前端问题暂缓、不阻塞本次合入；本轮记录该 reviewer 意见，但不把登记 backlog 当作豁免。全套仍失败，尚无用户对该门槛例外的明确裁决，不写成「本地替代检查全部通过」或合入条件已满足。
- 第 7 节已有部分本地行为回归证据，但没有五组逐项完整验收记录；第 5 组真实后端及页面验收仍未做，方向 A 不标记为完整「已验证」。两个历史卡片样本的 API 核查与用户观察仅为已有卡片的证据；浏览器 Network 轮询及展示延迟仍未单独验证。

## 9. 风险、未决问题与异议

- **注入与兼容接口已落实**：工作台提供 `finalize_message`，通过构造参数经 `CollaborationService → ActionStore → LifecycleStore` 逐层注入，使用接收层当前事务连接，无独立 commit；服务层不导入界面模块。`accept_result` 返回分类结果，`accept` 保留 bool 包装。运行时原子性与存储失败路径仍待验证。
- **superseded 展示规则已落实**：保留原文，消息使用已有 `cancelled` 状态并追加「回复已失效」说明，区分失效与协议失败，不新增状态、不遗留 pending、不覆盖已确立终态。取消、失效及重复结果的竞争与幂等仍待回归。
- **内部 inspect 扩展已补记**：`finalize_message` 的 disposition 增加内部值 `inspect`，核对关联后只读返回当前消息状态，用于同事务检查取消，不修改消息状态；它不是 `ReceptionResult` 第六类，也不是新增协议字段或数据库状态。DeepSeek 认为此扩展合理，本次同步 §4.1 契约，未改变同事务原则。
- **previous 分支已修正为幂等返回**：初轮 previous→raise 破坏重复调用兼容，后续经回归与 review 改为从 `execution_runs.state` 获取执行状态，按 `source_message_id` 恢复暂存原文并携带准确 message_id；仅 completed/failed/cancelled 允许后续派发，不重新执行 worker、不从消息 status 反推结果。读取原文用于协议解析与重复接收判定，不用占位文本冒充合法协议。用户报告 `test_execution_wiring` 14 项通过，含真实解析、首次 accepted 与重复 duplicate 断言；不扩大为所有真实后端恢复场景均已验证。
- **review P2 保留项**：DeepSeek 对澄清路径 `error='Invocation cancelled'` 提出 call 可能记为 failed 而非 cancelled 的语义疑点；尚未通过运行验证。接收层另有 stopped/quiescing、call 已取消及消息 inspect 检查，须结合具体分支验证，不能仅凭 error 字符串认定最终分类。本次仅记录疑点，不修改代码。
- 接收提交前中断识别、存储不可用反馈、执行域释放失败仍待验证；不能承诺数据库不可写时仍能可靠持久化错误，也不能以自动重跑填补窗口。
- **卡片问题已定位：非 bug，是位置问题（本次两个样本范围）**。协作卡片容器位于消息列表上方（collaboration_ui.js:3–5），此前未找到该位置；滚动到上方后用户找到了卡片。本次证据支持数据→API→前端渲染→用户可见的失败展示正常，未发现卡片渲染缺陷，不扩大为所有场景均无问题。
  - 数据/API：前轮只读查询确认 entry-1871、entry-1879 均为 failed；运行中 `GET /api/collaboration/tasks?conversation_id=default` 返回 HTTP 200，包含两样本及各自 reason。
  - 前端渲染与视觉观察：用户确认「分两阶段调研 TODO」显示「失败，3/20 轮，status/invitation conflict」，「分两阶段确认需求」显示「失败，5/20 轮，questions required only for waiting_user」。卡片的失败标签、轮次和原因均与数据库/API 一致；此处依据用户反馈，非 Agent 直接检查 DOM。
  - 验证边界：用户未找到 F12 Network，浏览器轮询频率、请求过程未单独验证；不将本次观察写成完整轮询测试通过。
- **消息终态缺陷已编码修复，效果待验收**：第 2、5 节记录历史 completed/failed 不一致；第 4 节方向 A 现已实施，不能据卡片正常或代码检视通过宣称运行时缺陷已验证消除。历史任务未重放、未自动修复。
- 方向 A 与接口决定已收敛；B 的改动成本优势保留为反方理由，实际成本尚无量化结论。用户已授权实施；剩余验证缺口及 review 疑点不因本次文档同步而关闭。

## 10. 变更历史

| 时间 | 原决定/记录 → 新决定/记录 | 原因 |
|---|---|---|
| 2026-10-28 19:56:08 +08:00 | 分散定位与 A/B 讨论 → 独立失败可见性草案，方向 A 细化待确认 | 沉淀 entry-1885 定位与 entry-1891 双方收敛；补齐事务接收及执行域衔接边界，保留未决接口，不代表已实施 |
| 2026-10-28 20:04:27 +08:00 | 草案/提案、接口待细化 → 文档与方案已决定，接口已细化、实施待授权 | 记录 entry-1899 逐点核查后的显式传输结果、协作暂存、执行收尾、分类接收及同事务消息确认；补齐崩溃幂等边界，保留注入方式、superseded 展示与卡片待办 |
| 2026-10-28 20:26:23 +08:00 | 卡片不可见原因待验证 → 两样本运行时验证完成，非渲染 bug，是位置问题 | API 返回 failed+reason（HTTP 200），用户在消息列表上方找到两卡片并确认失败标签、轮次及原因；保留 Network 未单独验证边界，聊天消息一致性缺陷仍待修复，按用户要求明天再实施 |
| 2026-10-29 19:20:28 +08:00 | 方向 A 已决定、实施待授权 → 经授权完成编码，已实现待验证；DeepSeek 代码检视通过（无 P0），暂不能合入 | 记录四文件实施、构造参数注入、superseded 展示、内部 inspect 扩展及 previous→raise 不 replay 行为收紧；P1 文档缺口本次补记待核对，五组行为回归仍未执行，P2 重试兼容与澄清取消语义待验证，不把静态检查或 review 当完整验收 |
| 2026-10-29 19:42:38 +08:00 | 尚无行为回归结果、previous→raise → 针对性回归通过，previous 恢复幂等返回；全套 165 通过、1 失败、1 跳过 | 用户报告 issue8/direct_answer 28 项 OK、execution_wiring 14 项 OK；回归推动 stopped 顺序修复及 WorkerResult mock 适配。全套仍 FAILED，校正「166/167 通过」口径；前端 mock 问题登记 BACKLOG，reviewer 非阻塞意见与门槛豁免分开，真实环境验收仍待做 |
