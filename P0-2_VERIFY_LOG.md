# P0-2 验证过程与结果记录

> 本文件记录 P0-2（并发会话冲突）从前置探测到后续实施验收的**全部验证过程、证据与结论**，用于最终复盘。
> 未实测项一律标注「待实测」，不虚构未验证结论；每项按 `pass / fail / uncertain` + 证据记录。
> `pass` = 静态核对满足该检查；`fail` = 源码明确缺少目标机制；`uncertain` = 仍需实测。

## 记录规则

- **追加式记录（append-only）**：每次变更只追加、不覆盖既有结论。
- **时间戳**：每次追加标注时间点精确到秒，格式为本地时间 + 时区（如 `2026-10-25 14:32:07 +08:00`）。
- **三条边界**：
  1. 占位符「待记录 / 待实测」的首次填充属追加、不算覆盖。
  2. 对已填写结论的修正必须追加一条变更记录（含时间戳、位置、原值 → 新值、原因、证据），不修改原文。
  3. append-only 定位为协作约定，git 提交历史作为辅助留痕。

## 前置探测总体顺序

只读盘点与环境记录 → 受控进程树探测 → 两后端取消/恢复探测 → 隔离库迁移验证。
（执行边界与量化判据见 `P0-2_PROBE_PLAN.md`。）

---

## 一、只读盘点与环境记录

### 执行信息

- 执行日期：2026-10-25
- 执行者：Codex（盘点）/ DeepSeek（记录）
- 基线：`a04202e36034c82d520c05da6ab46bda8f7cc279`
- 方法：PowerShell `Select-String`、源码读取、Git 路径查询、目录属性检查；未导入业务模块、未连接运行数据库、未启动 CLI、未修改文件。

### 1. 写库入口职责清单

| 入口与调用方 | 表与事务边界 | 启动时写库 | 迁移暂停/拒绝现状 |
|---|---|---|---|
| `agent_bridge.py:25–74,196`，模块导入调用 `initialize_db` | `agents/messages` 建表及索引；`connect_db` 设 WAL；DDL 用 `executescript` | **是，导入即初始化** | 无迁移版本门禁或写入暂停机制 |
| `agent_bridge.py:76–193`，MCP 注册/发送/读取接口 | `agents/messages`；每次 `database()` 连接事务提交或异常回滚 | 初始化之外为请求时写入 | 无迁移协调；`list_agents`/`read_conversation` 也更新 `last_seen`，`read_inbox` 还更新 `read_at` |
| `web_chat.py:194–272`，`main` 调用初始化 | 四张 `workbench_*` 表；初始 DDL `executescript`；旧消息表重建 `BEGIN IMMEDIATE`；启动清理 pending 消息 | **是** | 无实例锁；启动会改遗留状态 |
| `web_chat.py:1241–1270`，设置接口 | 配置 UPSERT 与删除两类 session 绑定同一连接事务 | 否 | 仅当前进程内检查对话活动运行，无跨实例迁移门禁 |
| `web_chat.py:1350–1391`，两后端完成后保存 session | `workbench_sessions`/`workbench_agent_sessions`；各自单独 UPSERT 事务 | 否 | 无 generation、运行归属或跨域重复 ID 校验 |
| `web_chat.py:1432–1452,1639–1648`，结果更新、后端调用 | `workbench_messages`/`agents`；各函数独立事务 | 否 | 无迁移暂停机制 |
| `web_chat.py:124–150,2324–2384`，协作调用与普通消息提交 | `workbench_messages`；协作占位消息单独事务；普通提交 `BEGIN IMMEDIATE` 原子插入用户消息与各 Agent 占位消息 | 否 | 请求可持续写入，无迁移门禁 |
| `web_chat.py:1976–2056`，入口结果处理、错误处理 | 首次建 `collaboration_entry_results`；入口结果 INSERT 与消息 completed 更新同一显式写事务；失败处理另行提交，注册失败还写 lifecycle | 首次相关调用建表 | 入口持久化与后续服务注册分成多个事务，无整体迁移协调 |
| `collaboration_batches.py:14–132`，Store 构造及批次操作 | `collab_jobs/batches/tasks`；构造 `executescript`；运行操作共用 `transaction()`：`BEGIN IMMEDIATE` 后提交/回滚 | Store 构造时是 | 无实例/schema 版本门禁 |
| `collaboration_lifecycle.py:11–205`，继承构造及生命周期操作 | `collab_lifecycle/calls/authorizations`，并更新 jobs/tasks；构造 DDL，业务用上述事务 | 是；启动还调用 `interrupt_running` | 无进程身份核验，启动直接更新中断/取消状态 |
| `collaboration_actions.py:10–136`，用户操作及事件消费 | `collab_bindings/operations/events/user_updates`，联动 lifecycle/calls/tasks/jobs；构造 DDL | Store 构造时是 | 无迁移门禁；用户停止等操作会继续写入 |
| `collaboration_service.py:14–318`，服务构造/注册/驱动/重规划 | 经 ActionStore 事务更新协作表；构造建 authorizations；注册含多个独立事务 | 服务构造时是 | 线程锁仅约束当前服务对象，不能协调第二实例或桥接迁移 |

**写库盘点结论**：范围识别 `pass`；生产迁移协调 `fail`。`web_chat.py:2404–2412` 先初始化、再中断遗留协作状态、最后绑定端口，故端口冲突不能替代「任何初始化前拒绝第二实例」的实例锁；迁移时需协调执行服务、桥接写入及所有初始化入口，桥接正常消息写入不应强制持有执行服务实例锁。

### 2. 工作区身份核对

| 检查项 | 结果 | 证据及边界 |
|---|---|---|
| 当前仓库资源归属 | `pass`（仅当前路径） | `git rev-parse` 返回当前目录为顶层，git-dir/common-dir 均为 `.git`；当前目录及 `.git` 未显示链接属性 |
| 基础路径解析 | `pass`（有限能力） | `web_chat.py:1214–1238` 使用绝对路径、`expanduser/expandvars`、`resolve(strict=True)` 和目录检查 |
| 大小写、8.3 短路径、junction/symlink 别名统一 | `uncertain` | 未创建别名夹具；现有解析不能当成资源身份一致性已验证 |
| 父子目录重叠、同目录跨会话互斥 | `fail` | 未发现物理目录身份、重叠检测或工作区 lease；`web_chat.py:2238–2246` 仅阻止当前对话活动期间改目录 |
| worktree 共享 Git 元数据协调 | `fail` | 未发现 common-dir 资源锁；当前普通仓库检查不能证明多 worktree 并发安全 |
| 提交配置快照与启动前版本核验 | `fail` | 后端运行时重新读取工作目录；无提交快照/generation 验证链 |

### 3. Adapter 契约核对

| 检查项 | 结果 | 证据及改造输入 |
|---|---|---|
| Codex 输入输出盘点 | `pass`（静态） | `web_chat.py:1730–1820`：stdin 输入、JSON stdout、stderr/退出码、临时最终回复文件、`thread.started` ID |
| DSH 输入输出盘点 | `pass`（静态） | `web_chat.py:1865–1960`：stdin、profile/patch/env、JSON `session/sessionId`、`final/text`、error、退出码 |
| 统一进程管理入口 | `pass`（已有基础） | 两后端均调用 `run_managed_process`，`web_chat.py:1571–1636` 负责 Popen、管道及超时；完整进程树清理待探测 |
| Adapter 与持久化分离 | `fail` | 后端函数直接保存 session、更新消息和 last_seen；无独立 Adapter 契约实现 |
| Agent 身份与后端分发分离 | `fail` | registry 已有 adapter 字段，但 `web_chat.py:141,1986` 仍按 `agent_id == "codex"` 分支选后端 |
| session 事件及时捕获与恢复 ID 核验 | `fail` | communicate 结束后才解析；Codex 恢复未核对实际返回 ID，DSH 已有 ID 时也未验证返回 ID 一致性 |
| 契约是否足以覆盖全部事件 | `uncertain` | 现有通道可支持 launch descriptor 与解析结果设计，但工具事件、取消期间 ID、异常部分输出需后续夹具核验 |

### 结论与阻断项

- 入口与改造范围已识别；**当前代码尚未满足 P0-2 的并发隔离、迁移协调和 Adapter 分层要求**。
- 已识别缺口：实例锁、迁移协调、工作区资源隔离、统一 Adapter、session 条件写回（含恢复 ID 核验）均未实现。
- 这些静态缺口**不妨碍继续开展隔离原型探测**（前置可行性验证与生产实施验收已区分）。
- 补充旁路范围：`main.py`（文件保存会话、直接 `subprocess.run`、不写桥接库）与 `capability_probe.py`（诊断子进程）为独立入口，不能被宣称已被未来执行服务统一管理；本轮未运行它们、未读认证配置。

---

## 二、受控进程树探测（Job Object）

**记录时间**：2026-10-25 16:18:08 +08:00；证据目录 `probe_evidence/job-object-20261025-161547/`（汇总 `review.json`）。

**结论**：Job Object 基础清理可行，但**整项未判全通过**——已复现「先启动、后归入 Job」会遗漏子孙进程的风险；保留串行保护。

| 场景 | 结果 |
|---|---|
| 挂起创建 → 归入 Job → 恢复运行，取消整棵树 | `pass` 3/3 |
| 复制 Job 句柄：关闭第一个仍存活，关闭最后一个退出 | `pass` 3/3 |
| 父先退出、子孙仍存活，再关闭 Job | `pass` 3/3 |
| 超时触发后清理 | `pass` 3/3 |
| 既有 Job 环境下再次归入、嵌套 Job | `pass` 3/3 |
| **先运行并生成子孙，再将父进程归入 Job** | **`fail` 3/3：子孙不属于测试 Job，关闭后仍存活** |
| 显式 breakaway 尝试 | 3/3 被拒绝；外层与测试 Job 限制来源未分离，记 `uncertain` |

- 通过场景清理耗时约 2–5 毫秒，均确认受管进程句柄已退出，独立对照进程仍存活；测试 Job 句柄默认不可继承。
- 第一轮夹具漏加进程查询权限（访问拒绝）记 `uncertain` 原样保留；修正后另开第二轮，未覆盖首轮证据。
- 仍待验证：管理进程异常退出、创建到归入之间崩溃、创建时关联 Job、显式句柄继承、真实 Codex/DSH 启动链。

## 三、两后端取消/恢复探测（session）

**记录时间**：2026-10-25 16:23:40 +08:00；证据目录 `probe_evidence/session-20261025-162103/`（汇总 `review.json`）。

**结论**：整项 `uncertain`——两后端正常启动均被权限错误阻断，尚未验证 session 恢复；仅「早期无 ID 取消的进程清理」通过。保留串行保护。

| 场景 | 本轮结果 |
|---|---|
| 排队取消 | `uncertain`：仅检查本地「不启动」夹具，未验证生产队列 |
| 启动后、未观察到 ID 即取消 | 两后端各 3/3 完成早期进程清理；仅此范围 `pass` |
| 正常完成对照 | 两后端均 `uncertain`，未取得 session ID 或最终回复 |
| 已知 ID、工具执行期间取消及恢复 | `uncertain`，未能建立测试 session |
| 已建立 session 的异常退出对照 | `uncertain`，启动错误不能替代该场景 |
| 取消 A 不影响 B、恢复+恢复、新建+恢复 | `uncertain`，未进入有效验证阶段 |

**阻断证据**：

- **Codex**：显式指定已有配置目录后，仍报 `failed to initialize in-process app-server client`、访问拒绝。
- **DSH**：已找到桌面端 CLI，但报 `EPERM`，无法写入配置的 headless profile。

- 早期取消清理耗时约 6–8 毫秒，已确认本次捕获的 Job 成员退出；所有 session ID 均记录为未知，无猜测绑定、未借用工作台 session、未自动重放。
- 本轮未修改生产代码、运行库、ACL 或验证日志；前两轮夹具缺陷与原始结果保留，第三轮修正记录另存。
- 后续需在具备所需权限的执行环境中，先取得正常完成对照，再继续恢复探测。

## 四、隔离库迁移验证

**记录时间**：2026-10-25 16:25:55 +08:00；证据目录 `probe_evidence/db-migration-20261025-162450/`（`probe.py` + `summary.json`）。

**结论**：隔离库原型验证通过——8 类场景各 3 次，共 24 次全部 `pass`。仅证明**原型机制可行**，尚不代表生产迁移已实现或验收通过。未打开运行中的 `agent_bridge.db`。

| 场景 | 验证结果 |
|---|---|
| schema 与版本号同事务 | `pass`：提交前独立连接看到旧状态；提交后同时看到新表、迁移数据与版本 |
| 重复启动幂等 | `pass`：重开连接重复执行，schema/数据/版本保持一致 |
| 中途失败回滚 | `pass`：分别在建表后、复制数据后、写版本后注入失败，均完整回退 |
| 一致性备份恢复 | `pass`：从仍打开的 WAL 模式源库备份；迁移并修改后恢复，逻辑快照与完整性检查一致 |
| 旧 session 映射 | `pass`：Codex thread 与 DSH headless profile key 均保留来源，迁移状态设为 `needs_validation` |
| 跨执行域重复绑定 | `pass`：同后端作用域重复 ID 被拒绝；不同作用域允许同名 ID |
| 旧数据存在重复绑定 | `pass`：整次迁移失败并回滚，不静默丢弃或改绑 |
| 不支持的新版本 | `pass`：拒绝迁移，原状态保持不变 |

- 使用人工构造的旧版数据及独立副本，隔离库与备份均保留供复盘。
- 待生产实施验收：真实数据映射、历史冲突处置、桥接写入暂停协调、实例锁、进程崩溃/断电恢复、generation 与运行归属条件写回。
- 本轮未修改生产代码或验证日志，串行保护继续保留。

## 五、复盘汇总

**记录时间**：2026-10-25 16:31:27 +08:00（第一次复盘，DeepSeek 牵头综合 Codex 评估）。

**阶段结论纠正**：四步探测都已开展并记录，**不等于前置验证全部通过**。可以开始「保留串行保护」的基础实现，但目前不能据此开启生产并发。最高优先级是**执行域与 session 的唯一写入保护**，其次是**可靠退出确认**与**工作区隔离**。

### P0-2 探测成果复盘与实施计划表

| 探测发现／待实现点 | 重要性 | 优先级 | 理由与后续动作 |
|---|---|---|---|
| 所有调用入口统一执行域 `(agent_id, conversation_id)`，同域串行 | 至关重要 | 高·核心首位 | 直接防止同一 session 多写入者；普通聊天/入口/owner/collaborator/final 必须全部接入 |
| 取得执行域后读 session；generation＋运行归属条件写回；恢复 ID 核验 | 至关重要 | 高·核心首位 | 防首次并发重复建 session、旧运行覆盖新绑定、恢复串线 |
| 同后端配置作用域内跨执行域重复 session ID 拒绝 | 至关重要 | 高 | 两执行域指向同一 session 时域锁也防不住；原型约束已通过，需落实真实数据与冲突报告 |
| 取消/超时后确认完整进程树退出；未知清理状态保留资源 | 至关重要 | 高 | 旧 CLI 仍存活时释放执行域会再并行写入；须与取消登记/启动许可/重启恢复共同实现 |
| 晚归入 Job 遗漏子孙（启动归属竞争窗口） | 至关重要 | 高·进程方案定稿前 | 已有反例，不能采用「正常启动后再 Assign」；挂起后归入仅解决提前生子，还需验证创建与归入之间崩溃 |
| 两后端正常完成、已知 ID 取消恢复、取消隔离、恢复组合 | 至关重要 | 高·尽早补测 | 权限阻断不能证明后端不支持/恢复安全；可并行基础实现，发布前须取得有效证据 |
| 数据库级执行服务实例锁（先于初始化和遗留处理） | 至关重要 | 高·数据层先做 | 否则两服务各自认为持有执行域；第二实例可能先改状态再因端口冲突退出 |
| 生产迁移：同事务/幂等/失败回滚/WAL 备份恢复/版本门禁 | 至关重要·数据安全 | 高·真实升级前 | 原型 24/24 说明机制可行，可转生产实现；无需重复合成实验凑数 |
| 桥接写入与迁移协调 | 至关重要·升级期间 | 高·真实升级前 | 实例锁不能阻止合法桥接写入；须明确迁移期间暂停/拒绝/重试，并覆盖导入即初始化入口 |
| 工作区物理身份、别名与父子重叠识别、整次调用独占租约 | 至关重要·文件安全 | 高·开放并发前 | session 不冲突也可能同时改同一文件；大小写/8.3/junction/symlink 是防绕过资源锁的边界 |
| 提交配置快照、启动前复核、切换目录/后端配置使旧 session 失效 | 至关重要 | 高 | 防排队任务启动时用另一目录/配置并把旧结果写回新绑定；随 session 数据模型实现 |
| worktree 共享 Git 元数据协调 | 至关重要·若支持 worktree 并发 | 中·启用前升为高 | 独立工作树仍可能共享 Git 资源；先保守串行/拒绝，后置精细协调 |
| 统一 Adapter、身份按 registry.adapter 分发 | 至关重要·用户要求可扩展 | 高·首版架构 | 决定新增 Agent 是否继续绕开统一保护；最小契约覆盖启动描述/实际 ID/结果文件/错误/权限能力 |
| Adapter 完整工具事件展示、详细诊断界面 | 一般 | 中/低 | session ID 与终态/错误解析优先；额外事件展示后置 |
| 容量、队列边界、多资源原子预留 | 至关重要·稳定运行 | 高 | 多域并发后须避免死锁/无限排队/超量启动；数值调优可后置，语义不能省 |
| `main.py` 独立入口 | 一般·共享 session 时升为至关重要 | 中·先明确边界 | 不能默认已纳管/无风险；确认是否可能操作同一后端 session，若是需接入保护或明确禁止并行 |
| `capability_probe.py` 诊断进程 | 一般 | 低/中 | 未见直接造成 writer 冲突；可保留独立诊断，但需有界超时并约束共享 Git 资源操作 |
| 已通过清理耗时继续压缩、追求更多重复次数 | 无所谓 | 低·放弃本轮 | 已低于约定上限；当前价值在补未覆盖边界 |
| 远程后端、只读工作区并发共享、同 job 批量并发 | 无所谓·本轮范围外 | 低·明确延期 | 不属于首版目标，加入会扩大验证面；保留 Adapter 扩展边界即可 |

### 建议实施顺序

1. **并行补两处关键证据**：进程启动/崩溃归属验证；在可运行 CLI 环境补正常完成与恢复对照。
2. **先做基础层（保持串行）**：实例锁、生产迁移、统一执行/session 模型、最小 Adapter 契约及诊断。
3. **落实核心保护**：所有入口统一调度、同域串行、session 条件写回、取消/重启资源占用恢复。
4. **工作区保护后再验收并发**：别名及重叠资源、配置快照、共享 Git 边界；最后验证真实后端组合与取消隔离。

### 阻断分阶段门槛

- **进程启动方案定稿前**：必须解决晚归入反例及创建窗口崩溃。
- **真实数据库升级前**：必须具备实例锁、迁移协调、备份与回退。
- **开启生产并发前**：执行域、session、完整退出、工作区保护及真实后端验收必须闭环。
- **不阻断基础实现**：当前 CLI 权限问题、未完成的恢复探测；但它们仍阻断对应能力的最终验收。

### 验证层级说明（附）

日志开头「`pass`=静态核对」的定义**仅适用于第一阶段（只读盘点）**；后续章节（二/三/四）含动态原型结果，须分别注明验证层级（静态核对 / 隔离原型 / 真实后端）。

## 六、协作路由异常记录

> 新增小节（追加式，不覆盖前五节结论）。本次为「协作协议校验 / plan 生成」的 bug 取证，只读诊断，未改生产代码。

**记录时间**：2026-10-25 18:07:25 +08:00

**现象**：用户连续两次要求 Codex 审核 DeepSeek 的任务 1 建议（`workbench_messages` id=1145、1147），Codex 两次都生成了审核回答（id=1146、1148），但两条回答均被协作协议判定 `failed`，用户侧表现为「审核未生效」，被迫重发。

**根因（确认）**：Codex 返回的 `plan.tasks` 中，第一个无依赖任务（id=1146 的 `review-task1-startup-lock`、id=1148 的 `review-startup-lock-proposal`）只含 `task_id`/`agent_id`/`instruction` 三字段，**遗漏了必填的 `depends_on`**。协议校验要求每个 task 必须含 `agent_id`/`depends_on`/`instruction`/`task_id`，故报：

> `[Collaboration protocol incompatible] expected fields: ['agent_id', 'depends_on', 'instruction', 'task_id']; optional: []`

**附带发现**：id=1139（DeepSeek 以纯问答回答「P0-2 进行到哪了」，`completed` + 空 tasks）同样被判 `failed`，错误为 `initial plan required unless asking user or blocked`——入口纯问答场景被协议要求必须带 plan，属另一处待核实的协议兼容问题。

**证据**：只读查询 `agent_bridge.db` 的 `workbench_messages` 表（id 1137–1151），提取 `status` 与 `content` 中 `Collaboration protocol incompatible` 错误串；并从 id=1146/1148 的 `content` 中正则提取各 task 的字段键，确认首 task 缺 `depends_on`。

**定性**：协作协议校验 / plan 生成 bug（P0-2 协作调度范畴）；根因是「无依赖任务须显式写 `depends_on: []`」未被遵守。修正方向（待定）：协议侧放宽 `depends_on` 为可选并默认空数组，或 Codex 生成 plan 时始终补 `depends_on: []`。本次仅记录。

### 修复记录

**修复时间**：2026-10-25 18:16:57 +08:00（Codex 实施，DeepSeek 记录）。

**改动**（`collaboration_protocol.py`，3 处，无数据迁移）：
1. `validate_payload()` 入口 `payload = copy.deepcopy(payload)`，末尾 `return payload`（去掉末尾 deepcopy）——校验与归一不再污染调用者输入。
2. `validate_plan()` 把 `depends_on` 从必填改为可选：`_keys(task, {"task_id","agent_id","instruction"}, {"depends_on"})`。
3. `deps = task.setdefault("depends_on", [])`——仅对字段缺失补空数组；显式 `null`/字符串等非法类型仍拒绝，未知/重复/自依赖/循环/未知字段检查保留。

**Bug B 未改**：入口 `completed` 仍必须携带 `plan`（纯问答应带 `plan.tasks=[]`）；不把「缺 plan」与「空 tasks」混为一谈。

**回归结果**：指定八模块 `unittest` 共 **65 项：64 通过、1 跳过、0 失败**（foundation 11、entry 5+跳过1、issue8 4、direct_answer 20、service 6、actions 4、lifecycle 8、batches 6）；跳过项为需显式启用的真实 Codex CLI 测试。`git diff --check` 通过。

**未完成验证**：真实后端 CLI 未启用；当前运行服务是否已加载新代码未验证，未重启工作台、未重放之前失败请求。

### 协作调度派发异常（待核实）

**记录时间**：2026-10-26 18:48:14 +08:00（DeepSeek 记录，未改代码）。

**现象**：DeepSeek 作为 owner/最终答复者在收尾阶段**两次**返回 `request_collaboration` + 末行独立 `@Codex`，但 Codex 均未被调度（用户在工作台看不到 Codex 交互）；仅当 DeepSeek 以 entry 角色返回 `@Codex` 时，Codex 才被派发。

**初步判断（待核实）**：owner 角色（本项目中同时是 final_responder）的 `request_collaboration` 未被调度器当作「需派发协作者」处理，疑似被当作最终交付而丢弃了末行 `@Codex` 邀请。

**待核实**：是否确为 bug、确切根因（调度器对 owner 角色 `request_collaboration` 的处理缺失，还是 owner==final_responder 的预期限制）需进一步核验调度代码（`collaboration_service.py` 的 drive / 终端邀请处理）。本次仅记录现象与初步判断，不臆测定性为已确认 bug。

**优先级提升 + 新症状（2026-10-26 20:26:14 +08:00，用户反馈 + DeepSeek 补记，未改代码）**：

- **优先级提升**：用户反馈该 bug 影响用户体验（虽不严重、但「上一轮对话结束了」观感不友好），要求提高修复优先级，后续安排修复。
- **新症状**：owner 返回 `continue_work`（挂自任务）时，调度器同样**未平滑接续、而是结束本轮**，导致自任务未执行（实例：`record-4b-active-verification` 未落地，需用户再发消息重触发）。
- **同源判断**：与既有「owner 返回 `request_collaboration` + 末行 `@Codex` 邀请被丢弃」同源——owner==final_responder 的「继续/邀请」指令被调度器当作「最终交付」处理。根因仍待核实 `collaboration_service.py` 的 drive / 终端邀请处理。

**记账偏差观察（2026-10-26 20:43:05 +08:00，DeepSeek 读库记录，未改代码）**：冒烟测试读库时观察到两条 owner 最终交付 run（`source_message_id` 1331/1336、`source_call_id` 对应 owner 角色）`state=failed` 但 `exit_confirmed=1`、域已 `idle`；其余 entry/collaborator run 均为 `completed`。判断为 owner==final_responder 最终交付被记账为 `failed` 的又一表现（记账偏差，用户已正常收到交付内容、不影响功能）。根因待核实，不臆断定性。

### Codex 验证沙箱访问 dsh.cmd 权限受限（待定）

**记录时间**：2026-10-26 19:48:48 +08:00（DeepSeek 记录，未改代码）。

**现象**：Codex 验证进程（workspace-write 沙箱）在调用 `resolve_dsh_command()` 访问 `C:\Users\FHB\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd` 时，`candidate.is_file()` 抛出 `PermissionError: [WinError 5] 拒绝访问`；`shutil.which('dsh')` 在该进程内返回 null（PATH 未含该目录）。

**判断**：这是**沙箱对工作区之外安装目录的读限制**，不是生产故障——生产工作台无沙箱、DSH CLI 生产可用（本会话 `@DeepSeek Harness` 即通过该 dsh.cmd 链运行）；DeepSeek 自身沙箱 `shutil.which('dsh')` 与 `Path.is_file()` 均能正常访问该文件。

**待定（不着急）**：后续是否放宽 Codex 沙箱对 `AppData\Local\Programs\DeepSeek Harness` 的读权限，使 Codex 能在自身沙箱内完成真实后端验证。本次仅记录、不修改。

## 七、基础层实施 · 任务 1（OS 实例锁 + 无副作用启动入口）

> 追加式记录，不覆盖前六节结论。本任务为复盘计划六步中的第 1 步，仅落地「启动前实例锁 + 初始化无副作用」，不涉及迁移/执行域/session（后续任务）。

**记录时间**：2026-10-25 18:26:10 +08:00（Codex 实施，DeepSeek 记录）。

**改动点**：
- 新增 `service_instance.py`：数据库身份归一（绝对路径 + 大小写/junction 归一；硬链接无法可靠识别时明确拒绝）+ OS 排他锁（Windows 锁文件内核字节锁，句柄全生命周期持有、不可继承）+ 可选诊断表（仅记录、非锁权威、不凭 PID/时间判断可接管）。
- `web_chat.py` 的 `main()` 启动顺序改为：解析配置与数据库身份 → 获取实例锁（失败即报错退出，不写库/不 DDL/不改遗留）→ 预占端口（暂不接单）→ 初始化与遗留处理 → 接单。
- `agent_bridge.py` 移除模块级 `initialize_db()`，改为独立主入口显式初始化（保证「导入不改库」）；桥接正常消息写入不争抢执行服务锁。
- 协作 Store 构造延迟到取锁后的统一启动阶段。

**验收结果（新增 7 项测试全通过）**：第二实例与不同端口拒绝且库不变、进程强制退出后 OS 释放锁可重获、句柄不可继承、导入 agent_bridge 不写库、端口冲突不写库、正常初始化与异常清理、大小写/junction 别名共用锁与硬链接拒绝。

**回归结果**：九模块 `unittest` 共 **72 项：71 通过、1 跳过、0 失败**（新增实例锁 7 项 + 原八模块 65 项中 64 通过 1 跳过）；跳过为需显式启用的真实 Codex CLI 测试。`git diff --check` 通过。保留串行保护、未新增 schema、未开放跨群并发。

**未完成验证**：DSH 沙箱下锁文件 ACL 未验证；运行服务尚未加载本次改动（未重启）；真实后端 CLI 未启用。测试用默认 Python 通过 `PYTHONPATH` 加载项目 `.venv` 依赖完成（直接运行 `.venv` Python 遇 `_ssl` DLL 加载问题）。

### 重启后实测验证（2026-10-25 18:32:00 +08:00，DeepSeek 实测）

- **A 同进程锁语义（隔离库）**：获取成功、持有期再次获取被拒（报 `execution-service lock`）、退出后重获成功、`<db>.service.lock` 生成——全 PASS。
- **D 导入无副作用**：`AGENT_BRIDGE_DB` 指向不存在路径，`import agent_bridge, web_chat`（`PYTHONPATH` 指向 `.venv/Lib/site-packages`）returncode 0，db 与 lock 文件均未创建——PASS。
- **B 真实 `agent_bridge.db` 锁状态**：验证进程**成功获取**了锁 → 说明**当前运行的工作台进程尚未持有实例锁（仍是旧代码、任务 1 未生效）**。
- **C 锁文件存在**：`agent_bridge.db.service.lock` 存在（由验证脚本创建并释放，非运行实例持有）。
- **未在沙箱内跨进程验证**：异常退出释放锁由隔离测试 `test_service_instance.test_process_death_releases_lock` 覆盖；沙箱内 spawn 子进程捕获输出受限，未做真实跨进程。

**实测结论**：任务 1 锁逻辑与导入无副作用在隔离环境验证通过；但**当前运行工作台尚未加载任务 1 新代码**（未持有实例锁），需**重启工作台**后实例锁才真正生效。

### 真实环境验收（2026-10-25 18:40:26 +08:00，用户实测 + DeepSeek 记录）

用户重启工作台后，在另一 PowerShell 窗口执行 `python web_chat.py --port 9999`，第二实例报：

> `RuntimeError: Cannot acquire database execution-service lock: ...agent_bridge.db.service.lock`

**判定：任务 1 生产环境验收通过。** 依据：
- `msvcrt.locking()` 抛 `PermissionError: [Errno 13]`，是 Windows 锁冲突（`ERROR_LOCK_VIOLATION`）的表现，发生在加锁步（`service_instance.py` line 34）而非打开步（line 24），说明是锁已被第一实例持有、**不是文件 ACL/权限问题**。
- 第二实例在 `startup_server` 第一步取锁即被拒，**写库/建表/改遗留之前**退出；换 9999 端口也无法绕过同库实例锁。
- 第一实例（工作台）仍正常运行。
- 同时印证先前两项未验证点：运行工作台已加载任务 1 新代码并持锁；真实环境锁文件无 ACL 问题。

## 八、基础层实施 · 任务 2（统一迁移/版本门禁/桥接协调）

> 追加式记录，不覆盖前七节结论。本任务为复盘六步中的第 2 步，落地迁移/版本门禁/桥接协调，不涉及 runs/domain/session（后续任务）。

**记录时间**：2026-10-25 18:46:09 +08:00（Codex 实施，DeepSeek 记录）。

**改动点**：
- 新增 `database_schema.py`、`schema_definition.py`、`migration_barrier.py`、`test_database_schema.py`。
- 统一现有 DDL + `user_version=1` 门禁：未来版本、版本标记与表结构不一致时拒绝。
- 单事务迁移：失败回滚、SQLite WAL 一致性备份、显式恢复。
- 桥接/工作台/协作事务接入**共享许可**；迁移持有**排他许可**；普通事务不再隐式建表。
- 旧会话保留原表 + `needs_validation` 映射，拒绝未知键/冲突来源/重复绑定；正式 session 运行模型留待任务 3。

**验收结果（新增 10 项测试全通过）**：真实旧表迁移、WAL 备份恢复、幂等、DDL/数据/版本阶段失败回滚、提交前不可见、未来版本与不一致标记拒绝、重复/未知映射拒绝、跨进程共享与排他互斥、桥接事务许可及无隐式 DDL、工作台连接许可。

**回归结果**：十模块 `unittest` 共 **82 项：81 通过、1 跳过、0 失败**。`git diff --check` 通过。保留串行保护、未开放跨群并发。

**未完成验证 / 风险**：
- 生产库**尚未升级**（运行库仍是旧代码，未重启服务）。
- 首次部署必须先停止旧工作台及旧桥接写入者，再用 `--allow-legacy-upgrade` 显式确认升级；新屏障无法约束旧进程。
- DSH 沙箱 ACL、真实 CLI、断电恢复未验证。
- 正式 backend 配置作用域、generation、运行归属属于后续 session 任务。

### 迁移数据冲突与兼容修复（2026-10-25 19:05:10 +08:00，Codex 修复 + DeepSeek 记录）

**根因（确认）**：`_legacy_sessions` 白名单只认 `codex`/`deepseek-harness-headless`，而旧库 `workbench_agent_sessions` 实际含 `deepseek-harness`（早期 DSH key）与 `discussion:<hash>:<agent>`（已废弃讨论功能的孤儿 key），迁移在第一条 `deepseek-harness` 抛 `Unmapped legacy session key`。

**过程**：用户按方案 A 删除 3 条不兼容绑定（`deepseek-harness` + 2 条 `discussion:*`）后迁移成功（user_version=1）；这 3 条绑定仍保存在备份（`pre-cleanup-*.backup` / `pre-v1-*.backup`），未恢复进运行库。

**代码修复（非破坏性）**：修改 `database_schema.py` + `test_database_schema.py`：
- 未知 key、`discussion:*` 原样保留进 `legacy_session_imports`，标 `state='needs_validation'`，不再硬失败。
- 冲突/重复绑定用可逆来源限定标识（含来源表、原始 key、conversation_id）保存，不按时间覆盖、不合并；`SCHEMA_VERSION=1` 及原表结构/约束不变。
- 迁移成功后一次性报告全部待核验项；导入标识不得直接用作恢复 key。

**回归结果**：十模块 `unittest` 共 **85 项：84 通过、1 跳过、0 失败**。`git diff --check` 通过。

**未处理/边界**：已迁移 v1 库不会自动重跑导入，3 条删除绑定仍仅在备份；待核验候选的正式恢复与 backend 作用域判定留待任务 3；提交后 WAL 设置风险不在本次范围；真实 CLI 测试跳过。

## 九、基础层实施 · 任务 3（runs/domain/session 三表 schema + 旧数据映射）

> 追加式记录，不覆盖前八节结论。本任务为复盘六步中的第 3 步，落地执行域/session 三表 schema + 旧数据映射，不接执行入口（任务 4）。

**记录时间**：2026-10-25 19:11:13 +08:00（Codex 实施，DeepSeek 记录）。

**改动点**：
- 新增 `execution_runs`、`execution_domains`、`execution_sessions` 三表；`SCHEMA_VERSION` 1→2。
- domain/session 主键均为 `(agent_id, conversation_id)`；job/call/message 仅作 run 来源字段；session `UNIQUE(backend_scope, session_id)`。
- 旧表与 `legacy_session_imports` 保留；历史记录完整保存到 `legacy_candidates_json`，同域冲突不覆盖、未知与 `discussion:*` 独立、全部 `needs_validation`、不选择恢复 ID。
- 顺手修「提交后 WAL 风险」：`PRAGMA journal_mode=WAL` 移到 `BEGIN` 前执行并检查结果，失败明确报告 schema/data/version 尚未迁移。

**验收结果（新增 6 项测试全通过）**：v1→v2 迁移及幂等、全部候选与旧表保留、各阶段失败回滚、WAL 异常与模式未接受、domain 主键与 session 唯一约束、首次升级确认。

**回归结果**：`unittest` 共 **91 项：90 通过、1 跳过、0 失败**。`git diff --check` 通过。

**未处理 / open items**：
- 运行库仍为 v1，生产部署需停旧写入者 + 显式升级。
- 3 条已删除历史绑定仍仅在备份。
- 新表尚未接入执行入口（任务 4）。
- backend_scope 定义、条件写回、恢复 ID 核验留待任务 5。
- WAL 模式切换本身不属 DDL 事务，迁移失败时 schema/data/version 回滚，但已切换的日志模式可能保留（非破坏）。

### 生产升级验收（2026-10-25 19:18:37 +08:00，用户实测 + DeepSeek 记录）

**判定：P0-2 任务 1/2/3 生产环境验收通过。** 依据：
- v1→v2 生产升级成功：`PRAGMA user_version=2`，三表 `execution_runs`/`execution_domains`/`execution_sessions` 已建；`execution_domains=2`、`execution_sessions=2`，均 `needs_validation`（旧会话 codex + deepseek-harness-headless 正确映射）。
- 功能回归：普通聊天正常。
- 第二实例正确拒绝：`python web_chat.py --port 9999` 报 `Cannot acquire database execution-service lock`（`msvcrt.locking` 抛 `PermissionError Errno 13`=锁冲突 `ERROR_LOCK_VIOLATION`，发生在加锁步而非打开步，非 ACL）；第二实例在写库前被拒、换端口无法绕过，实例锁生效。
- 保留串行保护、未开放跨群并发。

## 十、可靠退出核验 + 4B 方案三点澄清

> 追加式记录（2026-10-26 19:19:58 +08:00，DeepSeek 记录）。针对「可靠进程退出核验 + 取消边界 + 4B 入口接入」方案三个 open items 的用户澄清与定稿口径。

**① Windows 版本与跨平台扩展**：首版限定 **Windows 10 及以上**（含 Win11、Server 2016/2019/2022），非「只能 Win10」；原因是 `PROC_THREAD_ATTRIBUTE_JOB_LIST`（创建进程时直接归入 Job）需 Win10/Server 2016+。跨平台扩展通过 `managed_process.py` 单模块隔离平台差异，数据层 `execution_tracking.py` 与入口接线（4B）平台无关；将来扩展 Linux（cgroup / `PR_SET_CHILD_SUBREAPER`）、macOS（`posix_spawn` + 进程组）时，仅在该模块加平台分支即可，数据层与入口逻辑改动极小、流程复用。当前平台分支不实现，只保证接口留位。

**② 5 秒时限的用户体现**：5 秒是「确认进程树全部退出」的等待上限，非硬承诺 OS 一定 5 秒内退净。正常情况（CLI 退出、后代随即退光）一检测到无活动进程就立即释放、用户无感；异常情况（CLI 退出但遗留后台子进程）最多给 5 秒收尾时间、仍存活则强制终止容器再确认释放；最坏情况（终止失败/证据缺失）保留 `quiescing` 阻断同域、需人工或任务 6 恢复。属安全优先策略参数。

**③ 容器外代理与嵌套 Job**：容器外代理 = WMI/外部服务/计划任务等代建的进程不在我们创建的 Job 内，`ActiveProcesses==0` 判据「看不到」它们，可能误判已退出而释放域、导致新旧进程重叠写文件；嵌套 Job = 工作台进程自身已被套入别的 Job，此时创建独立 Job 可能失败或行为异常。两者都需受控实测确认（Codex/DSH 两真实后端是否依赖容器外代理、当前环境是否嵌套 Job）。

**③·补：冒烟测试多层后代观察——2 个 codex.exe 来源待核实（2026-10-26 20:57:45 +08:00，用户反馈 + DeepSeek 记录，未改代码）**：冒烟测试第 4 步多层后代观察时，用户任务管理器持续看到 2 个 codex.exe（PID 41584、39156 附近），不确定是否为用户自己打开的 Codex 桌面客户端；DeepSeek 一度复查 `Get-Process codex` 为 0，说明无常驻泄漏，但来源未定。三种可能：①用户打开的 Codex 客户端（正常）；②冒烟取消后 Job 外辅助进程（容器外依赖）；③短暂残留已自行退出。处理：先记录、后续在「未打开 Codex 客户端」的干净状态下补测（只跑冒烟测试后观察 codex.exe 是否仍出现）；不臆断定性、不改代码、不杀进程。

### 两后端版本探测通过 + 进入 4B（2026-10-26 19:54:09 +08:00，用户实测 + DeepSeek 记录）

**结果**：用户在正常 PowerShell 运行 `verify_real_backends.py`，`codex --version` 与 `deepseek-harness --version` 均 `outcome=completed`、`returncode=0`、`exit_evidence=confirmed`（codex-cli `0.164.0-alpha.11.1` / dsh `0.2.1-rc.2`）。证明两 CLI 在 `managed_process.py` Job 容器下可启动、临时文件标准流收集正常、退出后 Job 归零（`ActiveProcesses==0`）；此前 Codex 验证沙箱的 `PermissionError WinError 5` 未在正常环境复现，环境缺口已解。

**边界**：版本探测**不等于**完整后端验收；new/resume、取消、真实会话多层后代、容器外依赖仍待验证，并入 4B 集成测试（接线后在工作台观察 @Codex/@DeepSeek 真实行为）。用户已确认继续 4B。

### 4B 入口接线实施完成 + 审核通过（2026-10-26 20:15:05 +08:00，Codex 实施 + DeepSeek 记录）

**改动**：修改 `web_chat.py`（`startup_server` 取实例锁后生成 `SERVICE_INSTANCE_ID`；`run_managed_process` 改用 `Execution.launch` 经 `managed_process`；`run_agent_task`/`invoke_collaboration` 建 run + 真实 `call_id` + release 早于 kick）、`collaboration_service.py`（`call`/`replan` 传真实 `call_id`、`_invoke` 检查 `dispatch_allowed` 停 `drive`）、`execution_tracking.py`（`set_launch_snapshot` 启动前冻结快照）；新增 `execution_runtime.py`（`Execution` 类，锁顺序成文：per-run lock → MigrationBarrier → BEGIN IMMEDIATE）与 `test_execution_wiring.py`。

**验证**：14 项隔离集成测试通过；相关回归 71 项通过；广回归 141 项 140 通过 1 跳过；`git diff --check` 通过。DeepSeek 审核 `approved`。

**边界**：未改运行库数据、未重启工作台、未开放并发、未写 session ready；真实后端 new/resume/取消/多层后代/容器外依赖待集成测试；前端 DOM 桩测试失败非 4B 引入。

### 4B 重启生效核验（2026-10-26 20:26:14 +08:00，用户重启 + Codex 只读核对 + DeepSeek 记录）

**结果**：用户重启工作台后，Codex 只读核对 `agent_bridge.db`——`user_version=2`；`execution_domains` 两行（`deepseek-harness/default` idle、`codex/default` running=本次调用）；`execution_runs` 两行（DeepSeek 入口 run=`11c1a7a4f12a4b858f4e6a54badf496e` source_job_id=entry-1325 call=entry message=1325 completed/exit_confirmed=1；Codex 调用 run=`284a0b2de1d241baab9a76c4e8297ab5` call=`744761c46f174859b698e108dabaceec` message=1326 running）；`instance_id` 均 `2e7cc5d6c8df40d3a72d5a758b98f4cb`；无遗留历史非 idle 域。

**判定**：4B 的执行域预留/释放、真实 `call_id` 传递、`SERVICE_INSTANCE_ID` 已在生产生效；但 new/resume、取消、多层后代、容器外依赖仍待集成测试。

### 4B 功能冒烟测试完成（2026-10-26 21:03:35 +08:00，用户实测 + DeepSeek 记录）

**结果**：用户按 `TEST_P02_SMOKE.md` 完成冒烟——自动检查 `smoke_test.py` 8 PASS / 0 FAIL / exit 0；new/resume/取消 两后端均通过（new=`launch_mode=new`、resume=`launch_mode=resume`、取消=`cancelled`/`cancel_requested=1`/`exit_confirmed=1`，域均回 idle）；多层后代无常驻残留（codex.exe 已退出，2 个 codex.exe 来源待核实）；容器外观察无意外文件/报错（git 改动均为实施+测试产物、DB 无常驻非 idle 域、`instance_id` 一致）。

**交付口径**：4B 代码 + 隔离验证 + 功能冒烟回归完成；完整边界、崩溃恢复、取消竞争、安全验收待 P0-2 完成后完整测试。

### 任务 5 数据层完成 + 审核通过（2026-10-26 21:40:41 +08:00，Codex 实施 + DeepSeek 记录）

**改动**：`execution_tracking.py`（`SessionIdentity`/`SessionClaim`/`SessionValidation`、`claim_session` 启动前认领 + generation 递增 + CAS、`_finish_session` 条件写回、`finish_run` 原子提交 session+run 终态+域释放）、`execution_runtime.py`（`claim_session`/`observe_session` 每次解析实际 ID、`validation_only` 启动即拒）、`web_chat.py`（`SESSION_CONFIGURATION_RESOLVER` 门控、`run_managed_process` 前后 `check_launch`、session 故障停止协作接续）；新增 `session_configuration.py` 与 `test_execution_sessions.py`。

**验证**：21 项新增测试通过、118 项相关回归通过、`git diff --check` 通过、未新增 schema 列、未开放并发；DeepSeek 审核 `approved`。

**边界**：生产未切换（`SESSION_CONFIGURATION_RESOLVER` 默认 `None`，走 4B 兼容路径）；真实命名空间/profile 隔离、受限候选验证传输、候选用户操作入口三件事待后续，任务 5 整体未完成。

### 任务 6 方案讨论 + 二次复审修订（2026-10-27 18:18:02 +08:00，Codex 提案+复审 + DeepSeek 复核记录）

**① 方案（T1–T6）**：启动保留隔离（各域态不凭重启释放）、持久化恢复证据走 schema v2→v3 正式迁移（恢复事件/审计表）、首版离线 CLI（inspect/verify/release）、session 三分支、11 条验收。

**② 二次复审 S1–S5 并收敛**：
- **S1 跨崩溃恢复正向路径缺失** → 机制验证先行：Job 打开失败≠退出证明，需完整证据链（持久化阶段/容器归属/命名空间/错误分类）+ 官方语义推理链 + 跨崩溃正反测试；
- **S2 隔离无解除规则** → 首版纳入显式新建 session 入口：旧执行退出确认+归属匹配后原子归档旧候选、推进代次、解除对应隔离原因、进「待新建」，创建失败不回退旧 ID，双路径+resolver 切换不绕过；
- **S3 范围矛盾** → 移除主机启动身份必过门槛（首版不依赖跨主机重启释放）；
- **S4 释放正确性** → 证据绑定 db/run/instance/fence/启动尝试/容器、只开已有 Job（禁用创建接口冒充空 Job）、release 持锁重新核验、同操作 ID 异参数拒绝、原子提交；
- **S5 历史与回退** → 历史缺证据占用首版能展示不能保证解除，v3 回退保留新库/隔离/审计不直接覆盖。

**③ 交付口径**：方案修订双方一致，技术机制尚未实测验证，任务 6 未实施；修订顺序 = 先机制验证 → 再 schema v3 + 离线 CLI + session 隔离生命周期；机制不闭环则报任务 6 未完成并重新讨论技术选择，不降格目标。

### 任务 6 机制验证通过（2026-10-27 18:29:05 +08:00，用户实测 + DeepSeek 记录）

**结果**：用户普通 PowerShell 运行 `verify_job_recovery.py` 返回 `exit_code=0`、`production_verified=false`。关键观察：
- `destroyed` 与 `never_created` 均 `error=2`（`same_error=true`）→ **错误码无法区分「已销毁」vs「从未创建」**，必须依赖持久化「容器已创建」记录；
- `access_denied` `error=5` → 权限不足可区分于「不存在」；
- `running_unique` 三层进程均 `exited` + Job 对象销毁 → **KILL_ON_JOB_CLOSE 生效**（唯一句柄关闭 → 整棵进程树被系统终止）；
- `extra_handle_prevents_last_close` PASS → 管理者死亡 ≠ 最后句柄关闭，生产释放规则必须纳入此语义；
- `before_resume`/`running_unique`/`running`/`empty` 四窗口 `controlled_candidate=true` → **正向路径成立**（持久化 created 记录 + error==2 + 进程 exited 三合一可信判定进程树已终止）；
- `before_create`/`before_registration` UNKNOWN → 无 created 记录正确保持不确定，不误判。

**结论**：跨崩溃恢复机制**闭环成立**。证据链 = 持久化容器已创建确认（membership_verified + kill_on_close）+ `OpenJobObjectW` error==2 + 进程状态 exited/pid_absent/pid_reused。

**边界**：受控三层树 ≠ 任意后代完整性证明；`cross_logon_namespace_tested=false`（跨登录会话作用域）、名称重用/电源故障持久性留待后续设计。

**限定说明（2026-10-27 18:37:25 +08:00，Codex 复核 + DeepSeek 修正）**：上文「闭环成立」降级为「**受控实验正向路径成立，生产释放前提待补齐**」。原因：实验控制程序预持全部已知节点进程句柄并等待原对象退出（`verify_job_recovery.py:348/378`），真实恢复环境无此句柄与完整后代清单；本次成功窗口均 `exited`，未覆盖 `pid_absent`/`pid_reused`。补充澄清——生产释放充分条件 = `created` 记录（membership_verified + kill_on_close）+ `OpenJobObjectW` error==2（正确命名空间定位下 = Job 已销毁）+ `KILL_ON_JOB_CLOSE` 官方语义（最后句柄关闭 → 全部进程终止）+ 名称生命周期规则（随机 UUID 不重用）+ 无 breakaway 容器约束（后代继承 Job）；进程状态 `exited` 仅为辅助佐证，预持句柄仅为实验交叉验证手段，均非释放条件。生产释放前提待补三件事：①无预持句柄的独立正向测试；②命名空间定位与名称生命周期规则明确化；③无 breakaway 容器约束确认。

### 任务 6 生产释放前提补充验证结果（2026-10-27 18:51:05 +08:00，用户实测 + DeepSeek 记录）

**结果**：用户普通 PowerShell 运行 `verify_task6_prereqs.py` 返回 `exit_code=1`、`production_verified=false`。关键观察：
- `no_preheld_handles_positive` PASS → 无预持句柄的独立恢复路径**在配置未被篡改前提下成立**（recover 不枚举后代、不持句柄，仅凭 created 记录 + error==2 推导）；
- `extra_job_handle_blocks` / `root_exit_descendants_alive` PASS → 额外句柄阻断、根退后代活阻断均正确；
- 8 个合成分类器反例全 PASS；
- `context_production_reliability` / `live_reboot_logoff_pid_reuse` UNKNOWN（预期，真实重启/重新登录未测）；
- **`job_configuration_immutable_default_token` FAIL**：`opened_for_set=true`、`changed=true`、`write_dac_available=true` —— 即**同用户默认令牌的受管进程可打开自己的 Job、清除 `KILL_ON_JOB_CLOSE`、并取得 WRITE_DAC**。

**结论**：配置不可变约束**实测不成立**。若受管进程运行期间清掉 `KILL_ON_JOB_CLOSE`，则 Job 对象销毁时进程树可能仍存活（孤儿），`OpenJobObjectW error==2` 会被误判为「进程树已终止」，动摇「Job 销毁=进程树终止」推理链的生产可靠性，构成任务 6 **真实前置缺口**（非脚本 bug）。需讨论解决方案：名称不暴露 / 自定义 DACL / 受限令牌 / 威胁模型降级 / 恢复证据链补充。

**限定说明（2026-10-27 18:55:48 +08:00，Codex G1-G4 收敛 + DeepSeek 确认）**：①**纠正上文「清除 KILL_ON_JOB_CLOSE → Job 销毁且进程存活 → error==2 误放行」的未经验证推论**——Job 对象在「最后句柄关闭 **且** 全部进程退出」后才销毁；清除标志导致进程仍活时 Job 不销毁，恢复时 `OpenJobObjectW` 成功打开 → `BLOCK_JOB_EXISTS`（**保守阻断，非误放行**），真实后果是清理失败 + 遗留占用；**真正危险是 breakaway**（后代脱离 Job 破坏「后代覆盖」前提，Job 销毁时脱离进程仍活则 `error==2` 误判）。②**威胁模型明确**：P0-2 保证范围 = 防**意外并发冲突**，**非**防恶意同用户受管进程篡改 Job 配置（需写入文档，不悄悄降级承诺）；隐藏名称非安全边界；DACL 收紧可作防误改但同用户 SID 无法区分管理层与受管进程。③**最小下一步** = 两个针对性验证：只清除 kill-on-close 后关闭最后句柄，观察 Job 生命周期与 `OpenJobObjectW` 行为；breakaway 反例验证受管进程能否开启脱离。

**结果说明（2026-10-27 19:02:37 +08:00，用户实测 + DeepSeek 记录）**：用户普通 PowerShell 运行两个针对性验证脚本均 `exit_code=1`。**lifecycle**：`worker_configuration_change` PASS（old_flags=8192→actual_flags=0，清除 KILL_ON_JOB_CLOSE）、`last_job_handle_closed_workers_alive` PASS（root/leaf 均 alive）、**`live_job_name_reopen` FAIL**（进程 alive 但 `OpenJobObjectW error==2`，即 Job 名称已消失而进程仍活）。**breakaway**：`breakaway_counterexample` PASS（root=exited、leaf=alive、error==2）、**`naive_absence_rule_safety` FAIL**（created+名称不存在会误放行脱离 leaf）。**结论（观点变更留痕）**：**证伪 Codex G1「进程存活时 Job 不销毁」的修正**——实测显示 Job 对象在最后句柄关闭时即销毁，进程存活与否不影响名称消失；**证实 DeepSeek 最初担忧**——清除 kill-on-close → Job 销毁 + 进程存活（孤儿）→ `error==2` 被误判为「进程树已终止」→ **误放行**；breakaway 同样导致误放行。**综合**：持久化 created + 命名 Job + `error==2` 作为「进程树已终止」证据，在「清除 kill-on-close」与「breakaway 脱离」两个同用户可执行场景下均不可靠，任务 6 跨崩溃自动恢复机制在当前威胁模型下**不成立**，需重新选择技术路线。

**精确限定（2026-10-27 19:06:11 +08:00，Codex 补充 + DeepSeek 确认）**：上文「Job 名称已消失」的表述需精确为——实测证明的是「**名称无法重新打开（`OpenJobObjectW` 返回 error==2）**」，**未**证明「内核 Job 对象已被销毁」；「名称查找失败」≠「对象销毁」。这不改变「created + 名称缺失不能作为全部执行已终止的释放证据」的结论，但避免把「名称可打开性」与「内核对象生命周期」混为一谈。Codex 据此撤回 G1 并给出 R1–R4 技术路线：R1 权限边界（受限令牌/独立账户，成本高、兼容性未验证）、R2 保守部分交付（检查+隔离+证据展示+审计，属范围调整、不算任务 6 完成）、R3 独立监督进程（改善可观测性但 breakaway 后代已脱离、监督者自身故障未解）、R4 持久化退出证据（仅覆盖「退出已确认」窗口）。推荐立即撤销「名称缺失作为释放依据」，向用户提交范围选择。

### 方案 A 决策与限制（2026-10-27 19:09:33 +08:00，用户决策 + DeepSeek 记录）

**决策**：用户选择**方案 A（保守交付）**。任务 6 交付范围 = 检查 + 隔离 + 证据展示 + 审计 + 有证据的人工释放。明确：任务 6 原「**跨崩溃自动恢复闭环**」目标**未完成**，降级为人工解除。

**限制清单（逐条）**：
- a) 崩溃后无可信退出证据 → 保持隔离 `UNKNOWN`，**不自动恢复**；
- b) **撤销「名称缺失作为释放依据」**——`created` 记录 + `OpenJobObjectW error==2` **不能**作为「进程树已终止」证据；
- c) 人工解除分两种——**有证据的人工释放**（满足退出/归属/代次条件后操作）与**无证据强制清除**（人为接受并发风险、不标记「已确认退出」、不算安全恢复闭环）；
- d) 根 PID 消失、任务管理器找不到进程、点击「确认」均**不能补齐完整性证明**；
- e) breakaway 后代、外部委托、上下文守卫、历史缺证据占用**仍未解决**。

**方案 A 任务 6 重新收敛（2026-10-27 19:15:16 +08:00，Codex A1-A6 + DeepSeek 确认）**：接受 Codex 三点修正——①**撤回「零误放行风险」**（方案 A 撤销了已知错误路径，但证据完整性、实现缺陷、人为强制清除仍可能有风险）；②**「Job 活动进程数为 0」不是独立充分证据**（只覆盖当前关联进程，不覆盖 breakaway 后代/外部委托，降级为辅助观察）；③**选择方案 A ≠ 批准无证据强制清除**。收敛结论：**首版「有证据释放」仅支持「可信持久化退出证据」**（管理者在句柄有效时确认退出并持久化，绑定 run/instance/fence/attempt，需定义并验证生产路径，不能仅预置 `exit_confirmed=true` 字段）；**「名称缺失」「根 PID 消失」「Job 为空」一律不足以释放**；**`force-clear` 首版默认不纳入**（独立后续议题），无证据遗留占用首版无法解除（只能隔离 + 人工评估）。验收采纳 Codex A5 的 11 条。实施前置：可信退出证据生产路径、上下文守卫可靠来源、breakaway/外部委托/历史缺证据拒绝规则。

## 变更日志

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-10-27 19:30:50 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 业界方案调研 + 6A/6B 拆分 | 时间戳：2026-10-27 19:30:50 +08:00；位置：十、任务 6 业界方案调研 + 6A/6B 拆分；变更内容：调研结论：业界「自动释放」（TTL 租约/心跳/文件锁/Job/systemd 等）依赖「判定死亡」或「资源端护栏（fencing/内核强制）」，本项目缺护栏不可照搬；「状态机恢复」（DB 事务+CAS+幂等）可直接采用。采纳 6A/6B 拆分：6A=遗留占用展示+保守阻断（启动保留非 idle、只读 inspect、复用现有表、不铺 schema v3），6B=解除与恢复使用（后续独立、人工确认释放） | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 19:15:16 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 方案 A 重新收敛（A1-A6） | 时间戳：2026-10-27 19:15:16 +08:00；位置：十、任务 6 方案 A 重新收敛（A1-A6）；变更内容：Codex A1-A6 三点修正：撤回「零误放行风险」、「Job 活动进程数为 0」非独立充分证据、选择 A≠批准强制清除；收敛=首版有证据释放仅支持可信持久化退出证据（需定义验证生产路径）、名称缺失/根 PID 消失/Job 为空均不足以释放、force-clear 首版默认不纳入、无证据遗留占用首版无法解除；验收采纳 A5 的 11 条 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 19:09:33 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 方案 A 决策与限制 | 时间戳：2026-10-27 19:09:33 +08:00；位置：十、任务 6 方案 A 决策与限制；变更内容：用户选择方案 A（保守交付=检查+隔离+证据展示+审计+有证据人工释放），明确任务 6 原「跨崩溃自动恢复闭环」目标未完成、降级为人工解除；限制：崩溃后无可信退出证据→保持隔离不自动恢复、撤销名称缺失作为释放依据、人工解除分有证据释放/无证据强制清除（后者不算安全恢复闭环）、根 PID 消失/任务管理器找不到/点击确认均不能补齐完整性证明、breakaway/外部委托/上下文守卫/历史缺证据占用仍未解决 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 19:06:11 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 生产释放前提·精确限定 + 技术路线 R1-R4 | 时间戳：2026-10-27 19:06:11 +08:00；位置：十、任务 6 生产释放前提·精确限定 + 技术路线 R1-R4；变更内容：精确限定「名称无法重新打开（error==2）」≠「内核 Job 对象已销毁」；Codex 撤回 G1 并给 R1-R4：R1 权限边界（受限令牌/独立账户，成本高兼容性未验证）、R2 保守部分交付（检查+隔离+证据展示+审计，属范围调整不算完成）、R3 独立监督进程（breakaway 后代已脱离、监督者故障未解）、R4 持久化退出证据（仅覆盖已确认窗口）；推荐撤销名称缺失作为释放依据，向用户提交范围选择 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 19:02:37 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 生产释放前提·生命周期/breakaway 实测 | 时间戳：2026-10-27 19:02:37 +08:00；位置：十、任务 6 生产释放前提·生命周期/breakaway 实测；变更内容：用户跑两个针对性验证均 exit_code=1：lifecycle live_job_name_reopen FAIL（清除 KILL_ON_JOB_CLOSE 后进程 alive 但 OpenJobObjectW error==2，Job 名称消失而进程仍活）；breakaway naive_absence_rule_safety FAIL（脱离 leaf 存活但 error==2，created+名称不存在误放行）；结论=证伪 G1「进程存活时 Job 不销毁」、证实「清除 kill-on-close→Job 销毁+进程存活→error==2 误放行」，breakaway 同致误放行；持久化 created+命名 Job+error==2 证据链在两个同用户可执行篡改场景下不可靠，任务 6 跨崩溃自动恢复机制当前威胁模型下不成立，需重新选择技术路线 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 18:55:48 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 生产释放前提·G1-G4 收敛 | 时间戳：2026-10-27 18:55:48 +08:00；位置：十、任务 6 生产释放前提·G1-G4 收敛；变更内容：Codex G1-G4 纠正「清除 KILL_ON_JOB_CLOSE→Job 销毁且进程存活→error==2 误放行」为未经验证推论：Job 对象在最后句柄关闭且全部进程退出后才销毁，清除标志→进程仍活时 Job 不销毁→BLOCK_JOB_EXISTS（保守阻断非误放行），真实后果=清理失败+遗留占用；真正危险=breakaway（后代脱离破坏后代覆盖）；威胁模型明确=P0-2 防意外并发非防恶意同用户；最小下一步=两个针对性验证（清除标志生命周期 + breakaway 反例） | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 18:51:05 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 生产释放前提补充验证结果 | 时间戳：2026-10-27 18:51:05 +08:00；位置：十、任务 6 生产释放前提补充验证结果；变更内容：用户跑 verify_task6_prereqs.py exit_code=1/production_verified=false：no_preheld_handles_positive PASS（无预持句柄路径成立）、extra_job_handle_blocks/root_exit_descendants_alive PASS、8 分类器反例 PASS、context/live_reboot UNKNOWN（预期）；job_configuration_immutable_default_token FAIL（opened_for_set/changed/write_dac_available 均 true，即同用户受管进程可清 KILL_ON_JOB_CLOSE 并取 WRITE_DAC）；结论=配置不可变约束实测不成立，动摇「Job 销毁=进程树终止」推理链生产可靠性，构成任务 6 真实前置缺口，需讨论解决方案 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 18:37:25 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 机制验证·限定说明 | 时间戳：2026-10-27 18:37:25 +08:00；位置：十、任务 6 机制验证·限定说明；变更内容：Codex 复核修正「闭环成立」→「受控实验正向路径成立，生产释放前提待补齐」：实验控制器预持全部节点句柄、未覆盖 pid_absent/pid_reused；生产释放充分条件=created 记录+error==2+KILL_ON_JOB_CLOSE 官方语义+名称生命周期+无 breakaway；预持句柄仅为交叉验证非释放条件；待补三件事=无预持句柄正向测试+命名空间/名称生命周期规则+无 breakaway 确认 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 18:29:05 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 机制验证通过 | 时间戳：2026-10-27 18:29:05 +08:00；位置：十、任务 6 机制验证通过；变更内容：用户跑 verify_job_recovery.py exit_code=0/production_verified=false：destroyed 与 never_created 均 error=2(same_error，错误码不可区分)、access_denied error=5(可区分)、running_unique 三层进程 exited(KILL_ON_JOB_CLOSE 生效)、extra_handle PASS(管理者死亡≠最后句柄关闭)、四窗口 controlled_candidate=true(正向路径成立)、before_create/registration UNKNOWN；结论=跨崩溃恢复机制闭环成立，证据链=持久化容器已创建确认+error==2+进程状态；边界=受控树≠任意后代、cross_logon_namespace_tested=false | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-27 18:18:02 +08:00（原记录精度） | 原历史阶段 | 十、任务 6 方案讨论 + 二次复审修订 | 时间戳：2026-10-27 18:18:02 +08:00；位置：十、任务 6 方案讨论 + 二次复审修订；变更内容：Codex 提案 T1-T6（启动保留隔离+恢复证据 v2→v3+离线 CLI+session 三分支）后二次复审发现 S1-S5 并收敛：S1 机制验证先行（Job 打开失败≠退出证明、完整证据链+正反测试）、S2 首版显式新建 session 入口（防旧 ID 回流）、S3 移除主机启动身份必过门槛、S4 证据绑定+只开已有 Job+release 持锁重核验+同操作 ID 异参数拒绝、S5 历史 unknown 与 v3 回退约束；方案修订双方一致，技术机制尚未实测，任务 6 未实施 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 21:40:41 +08:00（原记录精度） | 原历史阶段 | 十、任务 5 数据层完成 + 审核通过 | 时间戳：2026-10-26 21:40:41 +08:00；位置：十、任务 5 数据层完成 + 审核通过；变更内容：Codex 实施任务 5（execution_tracking/execution_runtime/web_chat + 新增 session_configuration/test_execution_sessions）：session 认领+generation 递增+CAS+原子收尾、每次解析实际 ID、ready 联合门控、旧表读写退出；21 项新增测试+118 项回归通过、未新增 schema 列、未开放并发；DeepSeek 审核 approved；生产未切换（命名空间确认/受限验证/候选入口待后续），任务 5 整体未完成 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 21:03:35 +08:00（原记录精度） | 原历史阶段 | 十、4B 功能冒烟测试完成 | 时间戳：2026-10-26 21:03:35 +08:00；位置：十、4B 功能冒烟测试完成；变更内容：用户按 TEST_P02_SMOKE.md 完成：自动 8 PASS/exit 0、new/resume/取消两后端通过（取消 exit_confirmed=1 域 idle）、多层后代无常驻残留、容器外无意外改动；交付口径 4B 代码+隔离验证+功能冒烟回归完成，完整边界/崩溃/竞争/安全验收待 P0-2 完成后 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 20:57:45 +08:00（原记录精度） | 原历史阶段 | 十、冒烟测试多层后代观察·2 个 codex.exe 待核实 | 时间戳：2026-10-26 20:57:45 +08:00；位置：十、冒烟测试多层后代观察·2 个 codex.exe 待核实；变更内容：用户任务管理器持续见 2 个 codex.exe，不确定是否因打开 Codex 客户端；DeepSeek 复查 Get-Process 一度为 0 无常驻泄漏，来源未定（客户端/Job 外辅助进程/短暂残留）；先记录、后续干净状态补测 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 20:43:05 +08:00（原记录精度） | 原历史阶段 | 六、协作调度派发异常·记账偏差观察 | 时间戳：2026-10-26 20:43:05 +08:00；位置：六、协作调度派发异常·记账偏差观察；变更内容：冒烟读库：两条 owner 最终交付 run（msg 1331/1336）state=failed 但 exit_confirmed=1、域 idle，其余 entry/collaborator run 均 completed；判定 owner==final_responder 最终交付被记账为 failed（记账偏差，不影响功能），根因待核实 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 20:26:14 +08:00（原记录精度） | 原历史阶段 | 十、4B 重启生效核验 | 时间戳：2026-10-26 20:26:14 +08:00；位置：十、4B 重启生效核验；变更内容：用户重启后 Codex 只读核对：user_version=2、2 域 2 run、DeepSeek run completed/exit_confirmed=1 域 idle、Codex run running、instance_id=2e7cc5d6c8df40d3a72d5a758b98f4cb、无遗留非 idle 域；判定 4B 执行域预留/释放+真实 call_id+SERVICE_INSTANCE_ID 生产生效；new/resume/取消/多层后代/容器外依赖待集成测试 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 20:26:14 +08:00（原记录精度） | 原历史阶段 | 六、协作调度派发异常·优先级提升+新症状 | 时间戳：2026-10-26 20:26:14 +08:00；位置：六、协作调度派发异常·优先级提升+新症状；变更内容：用户反馈影响体验、要求提高修复优先级；补记新症状——owner 返回 continue_work 挂自任务时调度器未平滑接续而结束本轮、自任务未执行（record-4b-active-verification 未落地）；与 request_collaboration+@Codex 邀请被丢弃同源（owner==final_responder 的继续/邀请被当最终交付）；根因待核实 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 20:15:05 +08:00（原记录精度） | 原历史阶段 | 十、4B 入口接线实施完成 + 审核通过 | 时间戳：2026-10-26 20:15:05 +08:00；位置：十、4B 入口接线实施完成 + 审核通过；变更内容：Codex 实施 4B（web_chat/collaboration_service/execution_tracking + 新增 execution_runtime/test_execution_wiring）：执行域预留+Job 执行+条件释放+真实 call_id+恢复单 run+release 早于接续+锁顺序成文；14 项隔离集成通过、71 项相关回归通过、141 项广回归 140 通过 1 跳过；DeepSeek 审核 approved；真实后端深层验收待集成测试 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 19:54:09 +08:00（原记录精度） | 原历史阶段 | 十、两后端版本探测通过 + 进入 4B | 时间戳：2026-10-26 19:54:09 +08:00；位置：十、两后端版本探测通过 + 进入 4B；变更内容：用户正常 PowerShell 跑 verify_real_backends.py：codex 与 deepseek-harness --version 均 completed/returncode=0/exit_evidence=confirmed，环境缺口已解；版本探测不等于完整验收，new/resume/取消/多层后代/容器外依赖并入 4B 集成测试 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 19:48:48 +08:00（原记录精度） | 原历史阶段 | 六、Codex 验证沙箱访问 dsh.cmd 权限受限（待定） | 时间戳：2026-10-26 19:48:48 +08:00；位置：六、Codex 验证沙箱访问 dsh.cmd 权限受限（待定）；变更内容：Codex 验证进程（workspace-write 沙箱）访问 dsh.cmd 报 PermissionError WinError 5、shutil.which('dsh') 为 null；判定沙箱对工作区外安装目录读限制、非生产问题（生产工作台无沙箱、DSH CLI 生产可用）；是否放宽该目录读权限待后续决定，不修改 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 19:19:58 +08:00（原记录精度） | 原历史阶段 | 十、可靠退出核验+4B 方案三点澄清 | 时间戳：2026-10-26 19:19:58 +08:00；位置：十、可靠退出核验+4B 方案三点澄清；变更内容：①首版限定 Windows 10+（PROC_THREAD_ATTRIBUTE_JOB_LIST 需 Win10/Server2016+），跨平台经 managed_process.py 单模块隔离、数据层与入口平台无关；②5 秒为退出确认等待上限，正常无感、卡死保留 quiescing；③容器外代理与嵌套 Job 均需受控实测 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-26 18:48:14 +08:00（原记录精度） | 原历史阶段 | 六、协作调度派发异常（待核实） | 时间戳：2026-10-26 18:48:14 +08:00；位置：六、协作调度派发异常（待核实）；变更内容：DeepSeek 作为 owner/最终答复者两次返回 request_collaboration + 末行 @Codex 均未被调度器派发给 Codex，仅 entry 角色的 @Codex 有效；疑似 owner==final_responder 的 request_collaboration 被当最终交付丢弃；待核实确切根因 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 19:18:37 +08:00（原记录精度） | 原历史阶段 | 九、任务3·生产升级验收 | 时间戳：2026-10-25 19:18:37 +08:00；位置：九、任务3·生产升级验收；变更内容：v1→v2 生产升级成功：user_version=2、三表已建、domains=2 sessions=2 均 needs_validation；普通聊天正常；第二实例 --port 9999 正确报 execution-service lock；判定任务 1/2/3 生产环境验收通过 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 19:11:13 +08:00（原记录精度） | 原历史阶段 | 九、基础层实施·任务3 | 时间戳：2026-10-25 19:11:13 +08:00；位置：九、基础层实施·任务3；变更内容：新增 execution_runs/execution_domains/execution_sessions 三表、SCHEMA_VERSION 1→2、旧数据映射进 legacy_candidates_json（needs_validation）；顺手修 WAL 移 BEGIN 前；回归 91 项 90 通过 1 跳过 0 失败；运行库仍 v1，执行入口接入留待任务 4、backend_scope/条件写回/恢复 ID 留待任务 5 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 19:05:10 +08:00（原记录精度） | 原历史阶段 | 八、任务2·迁移兼容修复 | 时间戳：2026-10-25 19:05:10 +08:00；位置：八、任务2·迁移兼容修复；变更内容：非破坏性修复 _legacy_sessions：未知/冲突/ discussion:* 历史 key 保留为 needs_validation 而非硬失败，冲突双方可逆标识不覆盖不合并；回归 85 项 84 通过 1 跳过 0 失败；用户已删 3 条绑定仍仅在备份未恢复 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:46:09 +08:00（原记录精度） | 原历史阶段 | 八、基础层实施·任务2 | 时间戳：2026-10-25 18:46:09 +08:00；位置：八、基础层实施·任务2；变更内容：统一迁移/版本门禁/桥接协调：新增 database_schema/schema_definition/migration_barrier，user_version=1 门禁、单事务迁移、共享/排他写入屏障；回归 82 项 81 通过 1 跳过 0 失败；生产库未升级，首次需停旧写入者后用 --allow-legacy-upgrade 确认 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:40:26 +08:00（原记录精度） | 原历史阶段 | 七、基础层实施·任务1·真实环境验收 | 时间戳：2026-10-25 18:40:26 +08:00；位置：七、基础层实施·任务1·真实环境验收；变更内容：用户实测第二实例 --port 9999 报 execution-service lock 被正确拒绝（PermissionError=锁冲突非 ACL、写库前退出、换端口无法绕过、第一实例正常），判定任务 1 生产环境验收通过 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:32:00 +08:00（原记录精度） | 原历史阶段 | 七、基础层实施·任务1·实测 | 时间戳：2026-10-25 18:32:00 +08:00；位置：七、基础层实施·任务1·实测；变更内容：重启后实测：同进程锁语义与导入无副作用 PASS；但真实 agent_bridge.db 锁被验证进程成功获取，说明当前运行工作台仍是旧代码、未持有实例锁，需重启工作台后任务 1 才生效 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:26:10 +08:00（原记录精度） | 原历史阶段 | 七、基础层实施·任务1 | 时间戳：2026-10-25 18:26:10 +08:00；位置：七、基础层实施·任务1；变更内容：落地 OS 实例锁 + 无副作用启动入口：新增 service_instance.py、main 顺序改取锁→预占端口→初始化→接单、agent_bridge 去导入即初始化；回归 72 项 71 通过 1 跳过 0 失败；未验证 DSH 沙箱 ACL 与运行服务加载 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:16:57 +08:00（原记录精度） | 原历史阶段 | 六、协作路由异常记录·修复 | 时间戳：2026-10-25 18:16:57 +08:00；位置：六、协作路由异常记录·修复；变更内容：修复 depends_on 缺省容错（validate_payload 入口 deepcopy、validate_plan depends_on 可选默认空数组），Bug B 未改；回归 65 项 64 通过 1 跳过 0 失败；未重启工作台、运行服务加载状态未验证 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 18:07:25 +08:00（原记录精度） | 原历史阶段 | 六、协作路由异常记录 | 时间戳：2026-10-25 18:07:25 +08:00；位置：六、协作路由异常记录；变更内容：取证协作协议 bug：Codex 两次审核回答（id=1146/1148）的 plan 首 task 缺 depends_on 字段，报 expected fields 校验失败被判 failed；附带 DeepSeek 纯问答（id=1139）报 initial plan required。只读取证，未改代码 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 16:31:27 +08:00（原记录精度） | 原历史阶段 | 五、复盘汇总 | 时间戳：2026-10-25 16:31:27 +08:00；位置：五、复盘汇总；变更内容：第一次复盘：综合 Codex 评估形成实施计划表，最高优先为执行域与 session 唯一写入保护、可靠退出确认、工作区隔离；区分三个阻断门槛，保留串行保护 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 16:25:55 +08:00（原记录精度） | 原历史阶段 | 四、隔离库迁移验证 | 时间戳：2026-10-25 16:25:55 +08:00；位置：四、隔离库迁移验证；变更内容：记录隔离库迁移原型：8 类场景各 3 次共 24 次 pass（同事务/幂等/回滚/备份恢复/旧 session 映射/跨域防重复绑定/历史冲突回滚/未来版本拒绝），仅证明原型机制可行 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 16:23:40 +08:00（原记录精度） | 原历史阶段 | 三、两后端取消/恢复探测 | 时间戳：2026-10-25 16:23:40 +08:00；位置：三、两后端取消/恢复探测；变更内容：记录 session 探测：两后端正常启动被权限错误阻断（Codex app-server 访问拒绝、DSH headless EPERM），整项 uncertain，仅早期无 ID 取消进程清理 pass | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 16:18:08 +08:00（原记录精度） | 原历史阶段 | 二、受控进程树探测 | 时间戳：2026-10-25 16:18:08 +08:00；位置：二、受控进程树探测；变更内容：记录 Job Object 探测：基础清理 pass、晚归入 Job 遗漏子孙 fail、breakaway 来源未分离 uncertain；整项未判全通过，保留串行保护 | 原记录所列来源 | 原范围及验证边界保留 |
| 2026-10-25 16:14:10 +08:00（原记录精度） | 原历史阶段 | 文档头 | 时间戳：2026-10-25 16:14:10 +08:00；位置：文档头；变更内容：确立追加式记录规则（append-only + 秒级时间戳 + 三条边界） | 原记录所列来源 | 原范围及验证边界保留 |
