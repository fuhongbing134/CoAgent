# 调用流程图互审 — Grok 侧

| 字段 | 值 |
|---|---|
| 作者 | Grok |
| 任务 | `review-grok`（entry-2903） |
| 状态 | 有界一轮互审意见；供统一稿处理，**尚不代表三端最终共识** |
| 日期 | 2026-11-11 |
| 代码基线 | HEAD `209446f0408d32fc005b7036543ab79a2c2d53f3` |

方法：本轮全文重读 `CODEX.md`、`DEEPSEEK_HARNESS.md`、`GROK.md`（以磁盘正文为准；DSH 原稿实际 288 行，交接写 247 行）。先独立对照当前工作树源码形成判断，再回应两端已写互审。未改任何原稿、代码、配置或 Git，未启动 Agent，未渲染 Mermaid，未跑真实 CLI / 浏览器 / 取消 / 重启。编号 `Gxx` 为本侧问题，`GS` 为自稿遗漏；不覆盖 Codex `Fxx` / DSH `Dxx`。

## 用户期望基线（对照）

用户要求：三端各自画出「单 Agent 调用」和「多 Agent 协作」两类详细流程与解释，使不熟悉项目的人能看懂；画完后互看、讨论问题，达成一致后汇总一版。本轮只做互审落盘，符合该基线。不扩大到 Grok 启动参数、不改代码、不提前写统一稿。

## 规范归属（场景未变，本轮复核）

- **must_follow**：`agent-discussion-spec` v1.2.0 `specs/AGENT_DISCUSSION_SPEC.md`（独立观点、逐点证据、异议与预算）；`design-decision-record-spec` v1.2.0 `specs/DESIGN_DECISION_RECORD_SPEC.md`（用户期望基线、取舍留痕）；`document-management-spec` v1.0.0 `specs/DOCUMENT_MANAGEMENT_SPEC.md`（草稿新增、状态与局部检查）；`task-execution-spec` v1.0.0 `specs/TASK_EXECUTION_SPEC.md`（充分验证、证据范围、陈述对应实际动作）。四份正文本轮实际重读。
- **not_applicable**：`code-review-spec` v1.2.0——本轮无代码变更，源码阅读只用于核对流程图事实。
- **uncertain**：无。读取失败：无。

索引仍按计划由统一稿阶段集中更新，本轮只新增本文件。

---

## 独立判断

三份原稿都足以进入统一稿材料，**不能原样作为最终稿**。最影响新读者的是三条会被照着图理解错的路径：

1. **owner 与 final 为同一身份时，正常完成直接 `completed`，不再另开 `final` 调用。** DSH 图二/图三/实例 B·C，以及 Grok 场景 3，把这条画成「一定再打一枪 final」。
2. **一次后端调用的输入输出不是三端同构。** DSH 图一把 stdin + `type=final` 写成公共节点；Grok 正文「差别只在命令行和 stdout 取文本」过强（Codex 最终正文来自 `--output-last-message` 文件）。
3. **概览图的箭头顺序。** Codex 图 5 把占用释放画在 worker 返回之前；DSH 图四把 `collab_tasks` 终态画进 `accept_result` 同一事务；Grok 图 E 成功路径漏了 `finish_execution` / `dispatch_allowed` 闸门。

以上均可由静态代码裁定，**无需用户价值裁决**。在这些图文按代码纠正并经终审前，本侧不宣布三端最终一致。该保留意见不要求本轮追加讨论任务。

新手可读性建议：先用「点名 / 广播 / 已有 job 的用户操作」三类入口，再分「纯问答、自任务、多 Agent 计划」三条走通；图与正文冲突时以代码为准，不要靠脚注反转箭头。

---

## 一、对 Codex 原稿

### G01（必须修正）图 5 时序把占用释放画在 worker 返回之前

- **位置**：`CODEX.md` 第 139–160 行 sequenceDiagram；第 162 行正文已按正确顺序解释。
- **问题**：图中 `W->>D: 会话观测、退出及占用释放记录` 出现在 `W-->>S: WorkerResult` 之前，读者会理解成「先释放再返回」。
- **证据**：`web_chat.py` `invoke_collaboration` 193–213：`worker(...)` 在 `try` 内；`worker_reply`（1385，协作成功走 `stage_collaboration_reply`）发生在 worker 返回前；`finish_execution` 在 `finally`，发生在 worker 返回之后，然后才 `return InvocationResult`。`collaboration_service.py` 152–160 在 `dispatch_allowed` 为假时抛 `DispatchHalted`，之后才解析协议。
- **影响**：图与文相反。新读者照图会把「暂存 → 释放 → 闸门 → 接收」弄反。
- **建议**：统一图固定为 `CLI 完成 → 解析/暂存（消息仍 pending）→ worker 返回 → finish_execution 释放 → dispatch_allowed 闸门 → parse → accept_result（call/message）→ drive 另开事务更新 task`。正文说明不能代替改图。

### G02（必须修正）图 1 把「是否 entry」放在 worker 之后，且协议失败被「有计划或待澄清」吞掉

- **位置**：`CODEX.md` 第 40–49 行：落库与 202 之后才 `route_message`；`W --> Q{"entry回复？"}`；`J` 的否分支标成「有计划或待澄清」去登记 job。
- **问题**：收件人在落库前已经算好；entry 与否在起 worker 前已经决定；协议非法应 `fail_entry_message`，不得登记 job。
- **证据**：`web_chat.py` 2498–2501 先 `resolve_message_recipients` / `requested_recipients` 再 2571 落库；`run_agent_task` 2145 再 `route_message`，2170 在 worker 前选择 `entry_prompt(prompt)` 或原 `prompt`；2183–2185 解析失败走 `fail_entry_message`；2200 仅 `completed` 且 `tasks == []` 直接结束。
- **影响**：顺序读成「先跑、再看像不像入口」；协议失败可能被画成注册协作。
- **建议**：HTTP 层先路由再校验/落库；worker 内标「复核路由」；worker 前分叉 prompt；worker 后只做「协作则解析协议、普通则直接回写」。协议失败单独 `failed`。主要分支可吸收 Grok 图 C。

### G03（可采纳，范围限定）第三层三后端差异方向正确

- **位置**：`CODEX.md` 第 169–171 行。
- **本轮核对**：`run_codex` 1801–1880（stdin + `--output-last-message` 文件，`--sandbox` 取 `CODEX_SANDBOX` 或 final 时 `read-only`）；`run_deepseek_harness` 1940–2020（stdin，`--profile`/`--patch`/`--json` `-`，stdout `type=final`）；`run_grok` 2047 + `grok_adapter.py` 47–55（`--prompt-file`、`streaming-json`、`--permission-mode auto`/`dontAsk`，final 加 `--deny *` 与 `--disable-web-search`，**无** `--trust` / `--allow Bash`，另有常驻 `--no-subagents`）。超时三端默认 1200 秒（`web_chat.py` 97–99）。
- **限定**：以上为静态代码，未实测 CLI 行为。统一稿照录时写「以对应函数为准，未跑真实进程」。

### G04（表述可保留）图 2 / 图 4 对 owner=final 直通、ready[0] 串行、广播≠协作 的主体正确

- **位置**：`CODEX.md` 第 83、117–118、125–131、59–60 行。
- **证据**：`collaboration_lifecycle.py` 186–188；`collaboration_service.py` 227–229、249；`collaboration_entry.py` 18–26。
- **结论**：这三条应进统一稿。图 5 的时序错误不因这些正文正确而视为已解决。

---

## 二、对 DSH 原稿

### G05（必须修正）把 owner 完成一律接 `final_ready` → 再打 `final`

- **位置**：`DEEPSEEK_HARNESS.md` 图二 111–114、133–134 行；图三 169–180 行；实例 B 261 行、实例 C 263 行。
- **问题**：自任务和「owner=final 的多 Agent 收尾」被画成必定再开一轮 `final` 角色调用。
- **证据**：`collaboration_lifecycle.py` 180–188：无活动任务时，`owner_id == final_responder_id` 则 `phase=completed` 并保存 owner 文本；否则才 `final_ready`。`collaboration_service.py` 227–229 只在 `final_ready` 时 `call(..., "final")`。
- **影响**：多报一次调用、预算和权限阶段（final 才会切 Codex `read-only` / Grok `dontAsk`+deny）。当前 job 正是 owner=final=codex，按代码走 owner 完成即结束。
- **建议**：正常路径写清同身份直接完成；不同身份、额度不足或用户 `finalize` 才走专门 final。实例 B/C 同步改。

### G06（必须修正）把 stdin 与 `type=final` 写成三后端公共节点

- **位置**：图一 55–56、88 行；图四 210–211 行。
- **问题**：公共路径写成「stdin=prompt，stdout JSON，取 `type=final`」。这只描述 DSH。
- **证据**：见 G03。Grok 用临时 `--prompt-file`；Codex 最终正文来自 answer 文件，不是 stdout 的 `type=final`。
- **建议**：公共节点写「按后端传入提示并解析输出」；旁表列三端输入/输出/会话/权限。不要把 DSH 解析器画成唯一形状。

### G07（必须修正）授权与计划增量写窄了

- **位置**：第 185、188 行。
- **问题**：「只有 owner 邀请才授权」漏了 entry 初始化；「可新增任务或补依赖」读起来像可以改旧任务的 `depends_on`。
- **证据**：`collaboration_service.py` 59–64 `seed` 用 entry 末行邀请写入 `collab_authorizations`；`collaboration_lifecycle.py` 167–168 owner 的 `request_collaboration` **替换**授权名单。`collaboration_protocol.py` 84–89 旧任务定义（含 `depends_on`）不可变；只能追加新任务，新任务可依赖旧任务。
- **建议**：写「entry 初始化授权，owner 后续替换；只能追加新任务，不能改旧任务定义」。

### G08（必须修正）图四把 task 终态画进 `accept_result` 同一事务

- **位置**：图四 217–218 行「`collab_calls=completed + collab_tasks 更新 + 生命周期 phase 推进`」。
- **证据**：`collaboration_lifecycle.py` 172–174 协作者 `accept_result` 在校验后 `return result('accepted')`，**不**更新 `collab_tasks`。`collaboration_service.py` 259–261 在 `call` 返回之后**另开事务**把当前 task 标 `completed`/`failed`。`finalize_message` 与 call 接收同事务，task 终态不是该事务的一部分。
- **影响**：读者会以为协议接收失败会自动回滚 task，或 task 成功等于 call 接收成功。
- **建议**：精确图分开「接收 call/message 事务」与「drive 标记 task 事务」。

### G09（必须修正）取消图指向 `terminate_process_tree`，且把 drive 闭环说成依赖完成回调

- **位置**：图五 230–232 行；第 286 行「完成事件回调再 kick」。图二「无 ready → owner」未画「已有 reserved / queued / running 则等待」。
- **证据**：用户停止实际是 `web_chat.py` 1564–1578 `stop_active_run` → `Execution.cancel`；`cancel_collaboration` 138–151 再等待 `execution.done` 与 `released`。`terminate_process_tree` 1517 是进程管理底层，不是协作取消的入口。`_drive` 251 同步 `call`，259 更新 task，262 `continue`；263–264 **仅已有在途状态时 return 等回调**。正常串行主链不依赖前端回调。
- **建议**：取消链用 Execution/ManagedProcess；区分取消意图、UI 状态、退出证据与域释放。正常路径写「call 返回后同一 drive 循环继续」。

### G10（必须修正）图一两个分支同指 `R3`，且自任务被说成「整个 job 只起一个 CLI」

- **位置**：图一 70–71 行；第 94 行「物理上只起该 Agent 一个 CLI」。
- **问题**：`collaboration=True/False` 都进同一 `begin_execution`，看不出 prompt 分叉。自任务会 entry + 若干 collaborator + owner，同身份多轮 CLI。
- **证据**：`web_chat.py` 2170 在 worker 前选择 prompt；执行域键是 `agent_id + conversation_id`（`execution_tracking.py` 128–151），不是「一台物理机器、一次进程」。
- **建议**：图一补上 `entry_prompt` vs 原 prompt；注明同一身份可先后多轮 CLI。协作调用另有 `COLLAB_AGENT_LOCKS[agent_id]`（`web_chat.py` 164）。

### G11（建议补全）异常图过简，owner 决策漏 `decision_used`

- **位置**：图五孤立节点；图二 124–125 行无条件「决策→循环」。
- **证据**：`collaboration_actions.py` 29–85 支持 `answer`/`append`/`finalize`/`stop`/`set_limit`（2..100 且新值大于 used）；`quiescing` 分 `cancel_then_stop` / `cancel_then_finalize`。`collaboration_service.py` 222、265–269：一次 owner 决策后仍无可运行工作才 `blocked`。
- **建议**：用状态/操作表配连线；图二画上 `decision_used`。

---

## 三、对两端已写互审的逐点回应

先独立形成上面判断，再对照 `REVIEW_CODEX.md` / `REVIEW_DSH.md`。赞同均附本轮核对条件；不以多数意见代替代码。

| 编号 | 本侧态度 | 条件与补充 |
|---|---|---|
| F01 / G05 | **赞同必须修正** | 本轮重读 lifecycle 180–188。Grok 场景 3（`GROK.md` 283 行）犯同一错误，见 GS01。 |
| F02 / G06 | **赞同必须修正** | 本轮全文读了三后端函数。DSH D03/D04 声明未核 Grok/Codex 参数；本侧已核静态参数，仍标未实测。 |
| F03 / G07 | **赞同必须修正** | 针对 DSH。Grok 正文写「邀请才授权」覆盖 entry 与 owner，但未写清 entry `seed` 与 owner 替换的差别，统一稿应写明，见 GS05。 |
| F04 / G08 | **赞同必须修正** | 针对 DSH 图四。Grok 图 E 未把 task 画进 accept 事务，这点 Grok 更准。 |
| F05 / G09 | **赞同必须修正** | DSH 取消入口不对。Grok 267 行「进程没了」过强，见 GS02。 |
| F06 / G09 | **赞同必须修正** | 正常 drive 同步 continue。Grok 图 D 的 `continue` 回环主体正确。 |
| F07 | **赞同必须修正** | 这是 Grok 自稿错误，见 GS03。本轮核对：`do_POST` 2391–2420 对 `/api/messages` **无** Host 校验；`collaboration_http.py` 8–16 只对 action 强制 Host/Origin/token；监听 `127.0.0.1` 是绑定，不是该入口的应用层 Host 检查。 |
| F08 / G10 | **赞同必须修正** | 域键是 agent+会话。Grok 图 B「占用工作目录执行域」同样不准确，见 GS04。 |
| F09 | **部分赞同** | 「差别只在命令行和 stdout」过强，见 GS06。Grok 表格把三后端权限分列，不把 workspace-write 说成三端统一效果；表格本身可保留，但须加「静态代码、未实测」，并补上 Grok 常驻 `--no-subagents`。纯问答例子应用无需查证的问题；Grok 场景 1 用「仓库用什么语言」可能触发读文件，与图 C 的 `1+1` 不一致，见 GS07。 |
| F10 / G01 | **赞同必须修正** | Codex 自认。DSH 图四、Grok 图 E 成功路径也都漏了释放闸门，统一稿一次画对。 |
| F11 / G02 | **赞同必须修正** | Codex 图 1 顺序。DSH 图一须补入口解析。Grok 图 C 主要分支可采纳；Grok 图 A 把第二次 `route_message` 画在起线程之后，需标明这是 worker 内复核，HTTP 层路由在落库前，见 GS08。 |
| F12 | **赞同** | 异常表应含 waiting_user / answer / append / 过期 supersede。Grok 末行孤立 `)` 见 GS09。Mermaid 未渲染，统一稿须尝试或如实标注。 |
| D01 | **赞同** | 与 G01 同指 Codex 图 5。 |
| D02 | **赞同** | 与 G02 同指 Codex 图 1。 |
| D03 / D04 | **本侧已补核静态参数** | 见 G03。不把「代码里有这些开关」写成「已验证运行行为」。 |
| D05 | **赞同可确认** | 广播不走协议、`ready[0]`、owner=final 直通，Grok 正文 23/155/183 行正确；场景 3 与之矛盾须改。 |
| D06 | **本侧已核行号** | `recipientsForText` 确为 534–541；`composer` submit 确为 953–970。统一稿可保留，注明以当前工作树为准。 |
| D07–D11 | **赞同为 DSH 自稿遗漏** | 与 G10/G06/G11 及 COLLAB_AGENT_LOCKS、`decision_used` 一致。 |

无需要用户当场裁决的价值分歧。技术事实以代码为准。

---

## 四、回应自稿遗漏（GROK.md）

### GS01（必须修正）场景 3 与自身术语表矛盾：owner=final 仍「再打一枪 final」

- **位置**：`GROK.md` 第 283 行；对照第 23、155 行。
- **原表述**：当前这次任务「最后若 `final_responder_id` 仍是 Codex，由 Codex 再打一枪 `final`」。
- **证据**：与 G05 相同。本 job `owner_id=final_responder_id=codex`，owner 回 `completed` 且无活动任务时直接 `completed`。
- **原观点 → 新观点**：原稿把「专门 final 阶段」误用到同身份正常完成 → 同身份正常完成不另开 final；预算耗尽或用户 `finalize`、或 owner≠final 才走 `final_ready`。
- **影响**：新手会按当前任务以为互审之后一定还有一轮只读 final CLI。

### GS02（必须修正）重启恢复写成「进程没了」

- **位置**：第 267 行；图 F `KILL[events() 调 cancel 确认进程退出]` 也偏强。
- **证据**：`interrupt_running`（lifecycle 227–233）只改 lifecycle/call/task 行，不杀进程、不证明旧进程不存在。`cancel_collaboration` 等待 `execution.done` 与 `released`，失败则返回 False，事件保持 pending。`stopped` 视图在用户 stop 后即可出现。
- **建议**：写「控制状态标 interrupted / stopped；旧执行情况需核验；旧占用可阻断新执行」。

### GS03（必须修正）声称 `do_POST` 只接受本机 Host

- **位置**：第 58 行。
- **证据**：见上表 F07。
- **建议**：分清「服务器绑定 127.0.0.1」与「协作 action 的同源校验」。删除 messages 入口已有同等 Host 验证的断言。不把此事扩成安全修复任务。

### GS04（必须修正）图 B「占用工作目录执行域」

- **位置**：第 72 行。
- **证据**：`execution_tracking.py` 128–151 域主键是 `(agent_id, conversation_id)`；工作目录是运行记录上的配置/快照字段。
- **建议**：改成「按 Agent 与工作台会话预占执行域」。

### GS05（建议澄清）授权名单的入口 seed 与 owner 替换

- **位置**：第 185 行只写「邀请才授权」。
- **证据**：见 G07。Grok 未把「只有 owner 才授权」写死，但未说明第一次名单来自 entry 末行。
- **建议**：统一稿写「entry `seed` 初始化，owner `request_collaboration` 替换」。

### GS06（必须减弱）「差别只在命令行和 stdout 取文本」

- **位置**：第 68 行。图 B 的 KIND 分叉把「入口或普通」画成「直接写成 completed」（91–93 行）。
- **证据**：Codex 最终文本来自文件；会话 resolver、诊断元数据、权限开关、输出合法性（Grok 要求 `end` + `end_turn`/`EndTurn` 且唯一 sessionId）都不同。入口路径：`run_agent_task` **不**设置 `run['collaboration']`，`worker_reply` 走 `update_workbench_message`；`validating_entry` 时 completed 被改回 pending（1319–1337），只有 `entry_reply_received` 后才解析。图 B 把入口与普通归并成直接 completed，与第 230 行正文及图 C 矛盾。
- **建议**：公共包装保留；旁表列差异。图 B 入口分支改为「写入内容、消息保持 pending，再解析协议」。

### GS07（建议调整）纯问答例子不一致

- **位置**：图 C 用 `@Grok 1+1`；场景 1 用「这个仓库用什么语言？」。
- **问题**：后者按入口提示属于「有要执行的任务（包括读文件）」时不应 `completed`+空 tasks。
- **建议**：统一稿纯问答只用无需查证的例子。

### GS08（建议调整）图 A 把 `route_message` 画在起线程之后

- **位置**：第 49–52 行。
- **证据**：HTTP 层 2498 已路由并决定 recipients；线程里 2145 是复核并选择 prompt。
- **建议**：图上标两处：落库前路由；worker 前复核。

### GS09（必须清理）文末孤立 `)`

- **位置**：第 320 行单独一个 `)`。
- **建议**：删除。属残余符号，不影响流程结论。

### GS10（建议补全）图 E 成功路径漏释放闸门；表格漏 `--no-subagents`

- **位置**：图 E 213–227 行成功 alt；第 110 行权限行。
- **证据**：成功同样经过 `finally: finish_execution` 再返回；`dispatch_allowed=False` 时 service 不 parse。`grok_adapter.command` 50 行始终带 `--no-subagents`。
- **建议**：与 G01 同一张精确时序；表格补该开关。

自稿已做对、应保留进统一稿的部分：三类入口（点名 / 广播 / 任务卡片操作）；逻辑角色 ≠ 物理 Agent；广播不起协作协议；`ready[0]` 串行；CLI 成功 ≠ 协议接收；owner=final 直通（正文，不含场景 3）；图 C 入口失败/空任务/自任务分叉；`decision_used`；`COLLAB_AGENT_LOCKS`；三后端对照表骨架。

---

## 五、检查项（本轮协作审核，协作者范围）

1. 目标是否偏移：符合。仍是两类调用流程的互审，未改成启动参数或实现任务。
2. 是否扩范围：符合。只写 `REVIEW_GROK.md`，未改原稿/代码/Git，未启动 Agent。
3. 目标是否完成：符合。已全文读三稿、按代码给出位置/问题/证据/修正/自稿遗漏。
4. 结论是否有证据：符合。行号均指向本轮读过的工作树；运行态标未验证。
5. 缺口是否披露：符合。见下节。

文档检查（document-management-spec 新增草稿）：局部回读本文件；无 U+FFFD；引用的三份原稿与两份互审文件均存在。未更新 `docs/README.md` / `docs/drafts/README.md`（计划规定统一稿阶段集中更新）。未渲染 Mermaid（本文件无新图）。

---

## 六、保留异议、预算、未验证

- **保留异议**：F01–F11 / G01–G10 / GS01–GS06 所涉图文纠正并经终审前，不宣布三端最终一致。该意见不阻断统一稿继续整理。
- **无用户裁决议题**：当前分歧是可由代码判定的事实差异。
- **预算**：任务材料快照 used=7/20；本协作者调用后预计 8/20，以调度器为准。本轮为有界一轮互审，不追加讨论任务。
- **未验证**：Mermaid 语法与布局；真实浏览器发送与 DOM 刷新；真实三后端 CLI；取消 / 超时 / 重启注入；部署环境 session resolver 实际选择；`queued`/`running` 在正常成功路径是否被写入。不得把这些写成已实现的运行行为。

下一步：统一稿逐项处理 `F*`/`D*`/`G*`/`GS*`，以代码去重并记录采纳或否决；再交两端终审。原稿全部保留。
