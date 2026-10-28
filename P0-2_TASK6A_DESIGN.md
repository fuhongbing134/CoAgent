# 任务 6A 设计方案（遗留占用展示 + 保守阻断）

> 状态：设计定稿（经两轮复审收敛），**已有部分实现，完整验收待核实**。现有 execution_inspection.py、inspect_execution.py 及 execution_tracking.py 接入提供实现证据；本次未逐条验收，不据此宣称全部完成。
> 状态更新注记（2026-10-28 19:49:15 +08:00）：原文为“尚未实施。本文只陈述设计，不含任何已通过实现的验收结果”，保留为设计定稿时判断。P0-2_VERIFY_LOG.md 的“任务 5 数据层完成”属于 session 任务，不作为 6A 数据层完成的依据；6A 与 6B 边界保持不变。
> 本文是任务 6 拆分为 6A / 6B 后的 6A 部分；6B（遗留占用的解除与恢复使用）另列，不在此范围。

---

## 1. 目标与非目标

### 目标（6A 交付三件事）

1. 重启后保留旧执行占用，不凭重启释放。
2. 清楚显示「谁占用、最后记录是什么、为什么不能继续」。
3. 阻断限定到关联执行域及存在冲突的 session，不因某个遗留项停止整个工作台。

### 非目标（6A 明确不做）

- 不证明进程退出、不做退出证据链、不做自动恢复。
- 不提供 `verify` / `release` / `force-clear`（解除占用属 6B）。
- 不引入权限隔离（受限令牌/独立账户）、独立监督者、可信启动身份。
- 不新增完整 schema v3 迁移。
- 不自动重跑、不静默恢复旧 session。

**代价（如实披露）**：6A 完成后，遗留占用仍可能长期阻断使用；它不能解决「卡住后如何恢复使用」，那只属于 6B。

---

## 2. 数据设计：零新增持久化字段

复用 schema v2 现有三张表，不新增列、不升级 schema 版本。

| 表 | 已有字段及用途 |
|---|---|
| `execution_domains` | `agent_id/conversation_id` 定位；`state`、`owner_run_id`、`instance_id`、`fence` 描述占用 |
| `execution_runs` | `run_id`、`source_kind`、`source_job_id/source_call_id/source_message_id`、`instance_id`、`state`、`cancel_requested`、`exit_confirmed`、工作区及配置快照 |
| `execution_sessions` | session ID、身份范围、工作区、`generation`、owner、instance、`ready/needs_validation`、历史候选 |

### 查询时派生的字段（不落库）

`classification`、`reason_code`、`owner_relation`、`observed_at`、`exit_status=unknown`——每次查询实时计算，不写入数据库。

**F3 措辞约束**：`observed_at` 只表示「查询时刻」，**不冒充执行更新时间**；零新增字段意味着无法回答「卡了多久、最后在哪一步中断、旧进程是否还活着」，实施时所有展示与日志措辞须与此保持一致，不得推断。

### 两个如实披露的缺口

1. 三张表**没有执行阶段更新时间**：展示「最后记录阶段」，时间显示「未记录」；关联消息的 `created_at` 只能标为消息创建时间，不冒充最后执行时间。
2. `exit_confirmed` 是现有收尾记录，**不是带完整来源的实时退出证明**；6A 只展示原值，不解释为「当前进程退出验证通过」。

---

## 3. 启动核对：只分类，不改历史状态

启动顺序：

> 获得实例锁 → 完成既有初始化 → 只读核对占用 → 输出摘要 → 开放请求。

复用现有实例锁与每次启动新生成的 `SERVICE_INSTANCE_ID`；`begin_run` 已拒绝非 idle 域。

### 五类展示分类

| 观察 | 分类与处理 |
|---|---|
| 非 idle 域的 instance 与当前启动不同，关联 run 一致 | 「旧实例遗留占用；退出情况未知」，保留阻断 |
| 非 idle 域属于当前 instance | 「当前实例占用」；`quiescing` 额外提示退出未确认 |
| owner run 缺失，或域与 run 的 agent/conversation/instance 不一致 | 「占用记录不一致」，不自动修复，保守阻断 |
| 域 idle，但 session 仍持有 owner | 「session 残留认领 / 记录不一致」，不因域 idle 忽略 |
| 只有 `needs_validation` 历史候选，无执行 owner | 「会话待校验」，与「遗留执行占用」分开 |

「待核验/未知」是**展示分类**，不把数据库状态全部改成 `quiescing`，不新增 `unknown` 状态。启动摘要只提示，**实际准入必须重新查询当前状态**，不依赖启动缓存。

### 修正误导文案

现有初始化会把 pending 消息改 cancelled 并写「未完成任务已停止」——这不证明旧进程停止。对关联遗留占用的消息，改为：

> 工作台曾中断；此任务未完成收尾，旧执行退出情况未知，相关占用仍保留。

消息状态与执行状态分开解释，不为消息显示而释放域。

---

## 4. 只读 inspect

独立只读命令，避免导入 `web_chat.py` 触发应用初始化。拟定用法：

```powershell
& 'E:\Python\python.exe' -B '.\inspect_execution.py' --db '.\agent_bridge.db'
& 'E:\Python\python.exe' -B '.\inspect_execution.py' --db '.\agent_bridge.db' --agent codex --conversation <对话ID> --json
```

设计约束：

- 显式指定数据库，`mode=ro`；不存在则报错，不创建、不升级。
- 一次短只读事务取得一致快照，不调用 tracker 的写事务方法。
- **保留迁移屏障**；屏障不可用或 WAL 无法正确读取时**报错并提示重试**，不绕过协调、不显示「无占用」。允许必要旁路锁文件副作用，但不新增「跳过迁移协调」模式。
- 不启动应用、Agent、后台清理或 OS 进程查询。
- 离线独立命令无当前 instance，显示「当前实例关系未知」，不把「最新 run」猜作当前实例。
- 默认不输出配置快照、凭据、完整消息内容。

每项输出（示例）：

```text
对象：codex / 对话ID
分类：旧实例遗留占用（仅工作台内可准确比较当前 instance）
域：running；owner_run_id=…；fence=…
run：running；job_id=…；call_id=…；message_id=…
session：未统一认领 / 已绑定 / 历史候选 / 认领不一致
最后记录阶段：running
执行阶段时间：未记录
退出状态：未知
阻断原因：占用尚未正常收尾
提示：6A 仅供查看，不提供解除操作
```

session 展示同时读取旧 `workbench_sessions/workbench_agent_sessions`；`SESSION_CONFIGURATION_RESOLVER=None` 时，不把统一表无 owner 解读为「没有使用后端 session」。

---

## 5. 准入阻断三规则

### R1：插入新 run 前检查孤立 run

在 `begin_run` 的既有写事务内、**插入新 run 之前**，查询该域全部 `state IN ('pending','running','quiescing')` 的 run；存在任何一行即拒绝（孤立 run / 记录不一致），不自动修复。此时新 run 尚未插入，无需排除它。

### R2：残留 session owner 一律不覆盖

在 `claim_session` 首次认领前：

- `session.owner_run_id` 非空且 `!= token.run_id` → **一律拒绝覆盖**，无论该 owner run 是终态（completed/failed）还是非终态（pending/running/quiescing），也无论 owner run 缺失、instance 错位、cancelled、未知状态。
- `owner_run_id == token.run_id`（本 run 已合法认领）按现有调用契约处理，不伪装成首次认领。
- 无 owner 的普通历史候选仍按原规则处理，**不一律阻断**。

约束：普通入口、协作入口，以及 resolver 开/关两条路径**都**执行此规则。不调用「取消认领 / 改 needs_validation / 推进 generation」等写操作来消除异常（那些属 6B）。

### R3：事务内固定实际 session ID + 检查当前可见共享

- 在选择实际 resume ID 的**同一写事务内**：读当前绑定 → 检查该 ID 是否已被其他非终态 run / 非 idle 域持有 → 已占用则拒绝 → 返回**本次固定使用的 ID**；后续不得重新读取另一个 ID。
- **共享身份比较与保守匹配**：统一表按**已记录的后端身份范围**比较 session ID；旧表仅依据**当前可见绑定**关联执行域、未结束 run 和残留认领。**有可见共享迹象且存在关联占用时保守拒绝**；**仅缺少范围信息、没有共享冲突迹象时，标注「范围未知」，不因此全面阻断**。不跨后端仅凭相同 ID 字符串认定共享。
- 兼容路径（resolver=None）在同一事务内完成「读绑定 + 查共享 + 查占用」，结果绑定到本次执行；不能以「事务内查了一次」冒充「建立了持久占用关系」。
- **不将「当前绑定」当「历史执行实际使用哪个 ID」的证据**。

### F1（收尾要求）：拒绝后只收尾本次未启动的执行

准入拒绝或查询异常后，若本次确未创建进程，按既有 `not_started` 路径收尾本次 run/domain；**不得改变**导致阻断的旧 owner、session、generation。收尾写入失败则明确报错并保留占用，不谎报成功。此条防止「一次正常拒绝」制造新的遗留占用。

### F2：阻断提示分类

错误提示区分三类，避免用户误以为需反复重启或重试：

- **旧占用阻断**（遗留 run/域/残留 owner/共享占用）；
- **本次并发拒绝**（两个共享 session 请求相互碰撞）；
- **查询失败**（屏障/WAL 不可用、数据缺失、坏 JSON）。

### F4：双路径复用同一套共享匹配与拒绝原因规则

普通入口与协作入口、兼容路径（旧 session 表）与统一路径（认领逻辑）**复用同一套**共享匹配与拒绝原因规则，避免某条路径漏拦或错误收尾。零新增字段不意味着只加一个查询；实现成本须如实评估，不能仅凭 SQLite 单写事务就宣称已建立持久 session 认领。

---

## 6. 并发验收口径

固定共享 ID 的两个域并发准入，结果限定为「**最多一个启动，允许双方保守拒绝**」——两个域可能在选择 session 前均已预留，随后相互看到占用而都被拒绝。**不保证必有一个成功**。

实现时不能仅凭 SQLite 单写事务就声称已建立持久 session 认领。

---

## 7. 完整验收清单

**原 D6 八条**：

1. `reserved/running/quiescing` 跨重启保持原 owner、instance、fence 和状态，不自动释放。
2. 两种派发入口都拒绝占用域，且错误能定位到具体占用记录。
3. 能区分旧 instance、当前占用、离线无法判断及数据不一致。
4. 缺失 run、孤立 session owner、旧路径 session 信息不会被静默忽略。
5. 时间缺失、记录阶段、取消意图、退出确认字段的含义展示准确，不推断 OS 存活状态。
6. 重复 `inspect` 不改变业务数据、schema、generation 或占用，不启动任何执行；缺库和版本不支持时不初始化。
7. 无关且合法的执行域继续通过原准入规则；受影响域不自动重跑。
8. 首版不存在释放或强制清除入口；消息 cancelled 不被解释为进程已经停止。

**R1–R3 新增验收**：

9. 孤立未结束 run（缺域 / idle / 指向别 run）在普通与协作两入口都被阻断，且 `inspect` 能单列展示。
10. `claim_session` 首次认领前 owner 非空（任意状态）一律拒绝覆盖；owner 为空才允许；本 run 已认领按原契约。
11. 固定同一旧共享 ID 的两域并发准入，最多一个启动，允许双方保守拒绝。
12. 检查后绑定变化：本次仍用检查时固定的 ID，不重新读。
13. resolver 开/关两路径 + 无 owner 普通候选 + 无关不同 session 均不误伤。
14. `inspect` 保留迁移屏障，屏障/WAL 不可用时报错，不绕过、不显示「无占用」。
15. 统一表按后端身份范围比较 session ID；有可见共享冲突迹象且存在关联占用时保守拒绝；仅缺范围信息、无共享冲突迹象时标「范围未知」而不全面阻断。
16. 准入拒绝及查询异常后（双入口 + resolver 双路径），CLI 启动次数为零；本次 run/domain 按 `not_started` 正常收尾；旧 owner/session/generation 保持不变；收尾失败时明确报错展示。
17. 阻断错误提示能区分「旧占用阻断」「本次并发拒绝」「查询失败」三类。

实现验证用临时数据库 + 假执行适配器完成，无需再次运行真实崩溃实验。

---

## 8. 保留限制与边界

- 跨配置身份缺失、动态改绑、新 session ID 公布前窗口、外部写入**不在完整保证内**；不为此扩建恢复系统。
- 6A 不提供 `release` / `force-clear` / 自动恢复；「无证据强制清除」属 6B 独立决策。
- 6A 完成 ≠ 任务 6 整体完成。
- 6A 不要求先切换任务 5 的 resolver；必须同时理解当前兼容路径与统一 session 表，保持 resolver 原有设置。
- 「无关会话不受影响」的口径 = 没有共享冲突绑定、且满足现有准入规则的执行域不新增阻断，不保证所有标 ready 的会话一定可运行。
