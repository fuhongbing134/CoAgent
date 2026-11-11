# 调用流程图互审 — DeepSeek Harness 侧

作者：DeepSeek Harness。方法：全文阅读 `CODEX.md`、`GROK.md` 与自己的 `DEEPSEEK_HARNESS.md`，依据当前工作树源码静态核对；未改任何原稿、代码、配置或 Git，未启动 Agent。编号 `Dxx` 为 DSH 侧问题，与 Codex 侧 `Fxx` 独立，统一稿阶段合并去重。

基线：HEAD `209446f`。本稿只做有界一轮互审，结论供统一稿修订，不代表三端最终共识。

---

## 一、对 Codex 原稿的问题

### D01（时序顺序，需修正）Codex 图 5 sequenceDiagram 的释放与暂存先后有误

- 位置：`CODEX.md` 第 139–160 行时序图。
- 证据：`web_chat.py` `invoke_collaboration`（154–213）：`worker(...)` 在 `try` 内执行，worker 内部末尾才调 `worker_reply → stage_collaboration_reply`（暂存 content，status 仍 pending）；`finish_execution`（释放执行域）在 `finally` 里，发生在 worker 返回之后。
- 影响：Codex 图把 `W->>D: 会话观测、退出及占用释放记录`（对应 finish_execution）画在 `W-->>S: WorkerResult` 之前，等于「先释放再返回」，与代码相反。Codex 162 行文字说明已经按正确顺序写，但图与文矛盾，新读者照图会误解。
- 建议：时序改为 `CLI 输出 → worker 解析 → worker_reply 暂存 → worker 返回 WorkerResult → finally finish_execution → Service accept_result/finalize_message`。Codex 已在自身 REVIEW 中承认此类时序问题，此处独立确认并定位到具体箭头。

### D02（表述歧义，建议调整）Codex 图 2 把「是否 entry」的判断点放在 worker 之后

- 位置：`CODEX.md` 第 44–46 行 `W --> Q{"entry回复？"}`。
- 证据：`web_chat.py:2145` `run_agent_task` 先用 `route_message` 得 `collaboration` 布尔值，再据此决定 worker 的 prompt 是 `entry_prompt(prompt)` 还是原 `prompt`（2170 行）。即 entry 与否在**起 worker 前**已定，不是「跑完再看输出像不像 entry」。
- 影响：图上 `R → P → W → Q` 的连线易被读成「先执行、再判定是否入口」，顺序歧义。
- 建议：把 entry 判定前移到 worker 之前，`R` 的两个分支直接决定「传 entry_prompt」或「传原 prompt」，worker 之后只做「协作则解析协议、普通则直接回写」。

### D03（未核对，统一稿须标注）run_codex / run_grok 的精确 CLI 参数本轮未逐一核

- 位置：`CODEX.md` 第 169–171 行三后端说明。
- 证据：本侧本轮仅全文读了 `run_deepseek_harness`（1940）与 `build_agent_prompt`（1717），未全文读 `run_codex`（1800 起）与 `run_grok`（2047 起）。
- 影响：Codex 稿「Codex：exec/resume、answer 文件」「Grok：临时文件、streaming-json」的表述方向正确，但具体开关（sandbox 值、--deny、--disable-web-search 等）本侧不能背书。
- 建议：统一稿保留但注明「三后端 CLI 参数为静态代码阅读，未实测；以 `web_chat.py` 对应函数为准」。

---

## 二、对 Grok 原稿的问题

### D04（未核对，标注即可）Grok 图 B 三后端表格中的 CLI 参数与超时值

- 位置：`GROK.md` 第 104–112 行表格。
- 证据：`--sandbox workspace-write`、final `--deny *`、`--permission-mode auto`、超时 1200 秒等，本侧未全文核对 `run_codex`/`run_grok` 及超时常量。
- 影响：表格对「无 `--trust` / `--allow Bash`」的判断与既有会话结论一致（Grok 适配器未加这两个参数），但对 Codex sandbox、Grok deny 细节本侧不能独立确认。
- 建议：统一稿照录但统一加「静态代码，未实测」限定，避免把「没写参数」读成「已验证行为」。

### D05（可确认正确，无需改）Grok 对「广播不走协作协议」「串行 ready[0]」「owner=final 不另开 final」的表述准确

- 证据：`route_message` 返回 `([all], False)`（`collaboration_entry.py:19,26`）；`drive` 取 `ready[0]`（`collaboration_service.py:249`）；`accept_result` 中 `same = owner_id==final_responder_id → completed` 否则 `final_ready`（`collaboration_lifecycle.py:186-188`）。
- 结论：这几处是本轮最易被误读的点，Grok 处理正确，统一稿应保留其表达。

### D06（未核对，标注）Grok 图 A 引用的前端内嵌脚本行号

- 位置：`GROK.md` 第 57 行 `web_chat.py` 534–541、953–970。
- 证据：本侧未核对前端内嵌脚本（`recipientsForText`/`composer`）的行号。
- 建议：统一稿保留但注明「前端脚本行号以当前工作树为准」。

---

## 三、回应自稿遗漏（DEEPSEEK_HARNESS.md）

对照两端原稿，本侧原稿有以下实质遗漏，统一稿应补齐：

- **D07** 图一里 `R2` 两个分支箭头同指 `R3`，属装饰性分叉，未体现「collaboration=True 用 entry_prompt、False 用原 prompt」的差异（图二虽补了入口规划，但图一自身不完整）。
- **D08** 缺「同一物理 Agent 串行锁」：`invoke_collaboration` 用 `with COLLAB_AGENT_LOCKS[agent_id]:`（`web_chat.py:164`）保证协作调用时同一 Agent 同一时刻只跑一个 CLI。这是并发模型的关键约束，本稿漏画。
- **D09** 缺三后端 CLI 差异对照（输入方式 stdin/临时文件、最终文本来源、会话续聊参数），本稿只给了 DSH 单例；Grok 图 B 与 Codex 第 5 节均有，统一稿应含此对照。
- **D10** 异常/收尾图较简，缺 `set_limit`（2..100 且须留收尾空位）、`quiescing` 分 `cancel_then_stop`/`cancel_then_finalize`、`interrupted` 不自动重跑的明确语义（Grok 图 F / Codex 图 6 覆盖更全）。
- **D11** 图二 owner 决策循环未体现 `decision_used`：一次 owner 决策后仍无可运行工作才转 `blocked`（`collaboration_service.py:265-269`），本稿画成了无条件「决策→循环」。

---

## 四、保留异议与未决

- 三份原稿对「广播是否走 entry」「协作串行」「CLI 成功 ≠ 协议接收成功」「owner=final 直通 completed」四点已一致，本侧无异议。
- 未决仍是共同的运行态盲区：Mermaid 未渲染、真实 CLI/取消/超时/重启未实测、部署环境 session resolver 选择未确认。统一稿应把这些列为「未验证边界」，不得写死为已实现行为。

规范：must_follow `agent-discussion-spec` 1.2.0、`design-decision-record-spec` 1.2.0、`document-management-spec` 1.0.0、`task-execution-spec` 1.0.0 本会话已读复用；`code-review-spec` 1.2.0 不适用（无代码变更）。uncertain 无，读取失败无。本侧预算快照 used=6/20，本调用后预计 7/20，以调度器为准。
