# Collaboration protocol v1 — stage 1

> 历史记录，保留当时的协作设计与协议演进。现行运行时 JSON 的字段、状态和邀请校验见[协议解析器](../../../workbench/collaboration/collaboration_protocol.py)，讨论行为见[讨论规范](../../../specs/AGENT_DISCUSSION_SPEC.md)。

> Reading note (2026-11-10, entry-2647). The stage sections retain their original implementation snapshots. Later protocol decisions are recorded in the Issue #8 update below and in the [finalized design](../issue-8/ISSUE8_FIX_PLAN.md). See the [verification record](../issue-8/ISSUE8_VERIFY_TEST.md) for observed results and remaining boundaries.

Stage 1 provides `agent_registry.py` and `collaboration_protocol.py` as standalone
building blocks. It does not change the running workbench, route messages, parse
terminal invitations, schedule tasks, or call an Agent CLI.

Registry JSON is a list of records requiring `agent_id`, `name`, `aliases`
(list of strings), and `adapter` (`codex` or `dsh`). Optional `enabled` is a
boolean defaulting to true. Required fields and optional defaults are centrally
declared. Unknown fields fail with their names (to catch misspelled settings).
Future supported fields must add explicit validation and storage/adapter handling;
silently accepting a field would not implement its behavior. Existing configs
remain compatible when a new optional field has a default.
`AgentRegistry.from_file(path)` loads an actual UTF-8 configuration file.
Disabled identities cannot be resolved for execution. Aliases are case-insensitive;
collisions across identities and reserved broadcast names are rejected.

Payload fields:

- `protocol_version`: integer 1.
- `plan`: required for initial output, optional later. Contains `owner_id`,
  `final_responder_id`, and nonempty `tasks`. Each task has `task_id`, `agent_id`,
  `instruction`, and `depends_on` (task IDs). IDs must be unique; dependencies
  must exist and form an acyclic graph.
- `status`: `request_collaboration`, `completed`, `waiting_user`, or `blocked`.
- `handoff`: six nonempty text fields: `original_question`,
  `response_or_changes`, `evidence`, `rejected_alternatives`, `uncertainties`,
  `expected_action`. Use explicit "none" where appropriate.
- `questions`: list of nonempty question strings; nonempty exactly when waiting.
- `blocked_reason`: nonempty text required only when blocked.

`validate_payload` accepts invocation context (`role`, `phase`, `require_plan`)
and `invitation_ids` from the future terminal-mention parser. Invitation presence
must agree with status. Only entry/owner can invite; final delivery must complete.
`loads_payload` rejects duplicate JSON keys. Invalid input raises `ValueError`;
valid input returns an independent copy. Structural validation does not establish
that the plan correctly interprets the user's intent or that its evidence is true.
Task-to-invitation matching, readiness and persistent role enforcement belong to
the scheduler stage, not this standalone validator.

Stage 2 adds `collaboration_entry.py`: whole-message routing, the entry prompt,
and extraction of a dedicated `agent-collaboration` fenced JSON block after
readable text and before the terminal invitation line. An ambiguous initial
request may return `waiting_user` (or `blocked`) without a plan; other initial
responses require a plan. This avoids inventing roles just to ask a question.

The web runner sends explicit requests to the entry, validates its returned
envelope, and persists it in `collaboration_entry_results` keyed by message ID.
Malformed output retains the raw reply with a protocol error and failed status.
Stage 2 does not execute the resulting plan or auto-invite collaborators. The
existing page still displays the raw entry output; plan/card rendering is later.
The registry here still uses defaults; configurable identities and general
adapter dispatch in the existing workbench remain future integration work.

Run foundation tests (no mocks, real temporary configuration and child process):

```powershell
.\.venv\Scripts\python.exe -m unittest test_collaboration_foundation -v
```

These test actual registry/validator code, not LLM behavior. Live Agent protocol
generation and workbench end-to-end acceptance remain for the integration stages.

Stage 2 tests:

```powershell
.\.venv\Scripts\python.exe -m unittest test_collaboration_foundation test_collaboration_entry -v
$env:RUN_LIVE_AGENT_TESTS='1'
.\.venv\Scripts\python.exe -m unittest test_collaboration_entry.EntryTests.test_live_codex_semantic_plan -v
Remove-Item Env:RUN_LIVE_AGENT_TESTS
```

The opt-in test calls the real Codex CLI with a read-only planning request; it
does not substitute a response. Locally 14 non-live tests passed. The real CLI
attempt failed at process startup (exit 3221225794, no diagnostic output), before
semantic assertions. Web import was separately blocked by `_ssl` DLL loading.
These are unresolved validation limits, not live acceptance passes.

## Stage 3 batch foundation

`collaboration_invitation.terminal_invitation` now handles the terminal line,
including deduplication, registry validation, quoted/indented/fenced code
exclusion, and unknown/invalid invitation errors. Entry parsing uses it.

`collaboration_batches.BatchStore` persists original requests, immutable plans,
task dependencies, batches, terminal transport status, and complete result text.
The web entry runner registers validated plans as pending jobs (`entry-MESSAGEID`).
It does not dispatch them yet. `open_batch` explicitly selects ready tasks and
checks owner/invitation alignment. Owner work can be selected without self-mention.
Claims and batch consumption use SQLite write transactions to reject duplicate
dispatch/results. Only a fully terminated batch can be consumed, once. Failed
dependencies do not unblock subsequent tasks. Stop prevents late result writes
or batch consumption, but process-tree cancellation is not implemented here.

`execute_batch` accepts a real transport runner returning `(state, text)`.
Default execution is serial; parallel execution must be explicitly enabled after
the caller ensures workspaces and sessions are safe. Runner owns process timeout
and cancellation. Crash recovery does not automatically retry a running call.
Atomic consume is at-most-once delivery; durable owner invocation/recovery will
need a later transactional scheduling step (not an exactly-once CLI guarantee).

Tests use actual SQLite files, concurrent transactions, and actual Python child
processes. Manual status transitions test storage contracts, not Agent behavior.
They do not replace live LLM acceptance. Run:

```powershell
.\.venv\Scripts\python.exe -m unittest test_collaboration_foundation test_collaboration_entry test_collaboration_batches -v
```

Result: 19 non-live tests passed; 1 opt-in live test skipped. Full Agent dispatch,
owner resumption, quota, cancellation and UI remain future integration work.

## Stage 4 lifecycle foundation

`collaboration_lifecycle.LifecycleStore` adds persistent call reservations, default
8-round capacity, duplicate call rejection, identity/phase checks, and final
material assembly. Entry registration initializes usage to 1. Reservations charge
attempts before execution; collaborator batches retain capacity for owner review
and delivery. The owner continuation is separate from parallel owner task work.
Intermediate collaborator completion never finalizes, even for the designated
final responder. Owner completion blocks on unfinished planned work. Different
final identity enters `final_ready`; final calls must be explicitly reserved and
return a valid completed payload. Same owner/final reuses the owner answer.
Malformed owner/final results become failed, not completed. Explicit early/quota
delivery abandons pending tasks only after active work ends. Stopped jobs cannot
deliver. Recovery marks reserved calls interrupted rather than replaying them.

Integration boundary: batch transport and lifecycle reservations are still
separate APIs; the future orchestration service MUST reserve before every CLI
call and accept outcomes after transport. Inherited batch methods alone do not
enforce call budgets. No automatic CLI dispatch, UI pause, resume, or process-tree
cancellation has been enabled. These remain integration requirements, not passed
acceptance claims. Final prompt must enforce evidence-only/read-only delivery.

Stage 4 tests use real SQLite transactions and child processes plus explicit
protocol validation inputs, not fake Agents. Run all four test modules including
`test_collaboration_lifecycle`: 27 non-live passed, 1 live skipped. Real dual-Agent
owner resumption and final delivery, actual quota enforcement through Web, and
stop/cancel races remain final Web acceptance cases.

## Stage 5 user operation interfaces

`ActionStore` binds a job to its conversation, stores operation IDs plus request
content, persists user answers/updates, adjusts limits, and records a durable
outbox. Reusing an operation ID with different content fails. Answering is free
and changes waiting/blocked to working with a `resume_owner` event; only the
later Agent call consumes a round. Final material includes saved user updates.

Early finish cancels pending/reserved storage records and enters `quiescing`.
Only the internal transport acknowledgement `confirm_quiescent` allows
`final_ready`. Immediate stop sets the stopped flag and never delivers; it
supersedes pending early finish. Late results cannot advance either action.
These DB operations do NOT terminate OS processes or execute outbox events.
The future orchestration consumer must actually kill/wait for processes before
acknowledging quiescence, reserve resumed calls, and handle outbox recovery.

Workbench endpoint: `POST /api/collaboration/action`, UTF-8 JSON fields:
`job_id`, `conversation_id`, `operation_id`, `action`, optional `content` and
`round_limit`. Actions: `view` (no operation ID needed), `answer`, `append`,
`finalize`, `stop`, `set_limit`. Requires matching loopback Host/Origin and
`X-Capability-Token` from the page. Responses explicitly mark dispatch pending;
there is no automatic Agent continuation yet. Page/card controls are stage 6.
No-plan entry questions are still only entry results; assigning a plan and
creating their schedulable job after clarification is future integration work.

`test_collaboration_actions` uses a real loopback HTTP server, SQLite and
concurrent threads, with no mocks. Four tests passed. Full task cancellation,
actual resumed/final CLI calls and page controls remain Web acceptance tests.
# Issue #8 协议更新

协作入口只规划，返回 `continue_work`（自任务无需邀请）或
`request_collaboration`（末行邀请其他 Agent），不先执行计划中的操作。
旧式入口 `completed` 明确拒绝，保留原文，不自动执行或重跑。

负责人可使用 `continue_work` 追加后续任务；混合计划允许自任务与受邀任务共存。
追加计划、邀请授权、调用结果和生命周期更新在同一事务内接受。
既有任务和角色不可更改。协作者及最终答复者不能返回计划或 `continue_work`。
普通完成须无待执行任务及活动调用；显式提前交付仍走原有专门路径。
没有可执行或活动工作时最多一次负责人补充决策，仍无法推进则明确阻塞。
本次升级不自动恢复历史任务；部署前结束现有调用，重启后由既有恢复机制标记中断。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-20 11:21:44 +08:00 | entry-3821／协议归档 | 根目录历史稿→协作专题归档 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 历史正文及现行实现导航 |
