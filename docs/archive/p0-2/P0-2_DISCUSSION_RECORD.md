# P0-2 讨论记录与方案定稿

> 本文件单独记录 P0-2（并发会话冲突）的完整讨论、各方意见、已敲定方案、待考虑点与关键要点，作为后续按清单逐步实施与验收的依据。验证场景按用户要求留待方案落定后另行讨论。

## 一、问题与目标

- **P0-2 = issue #4「并发会话冲突（会崩）」**：同对话内两个任务交叉执行，Codex 调用报 `thread-store conflict: already has an active writer`。
- **用户目标（两条）**：
  1. 支持「群聊」模型——同一 Agent 可同时在多个群（conversation）做不同的事，真正并发；后续会加更多 Agent。
  2. 后续加入其他 Agent 时**改动最小化**（天然适配 / 只改注册或适配层）。
- **执行域固定为 `(agent_id, conversation_id)`**：同群同 Agent 串行，跨群受容量控制并发。session 按执行域管理，普通聊天、协作入口、collaborator/owner/final 全部接入同一执行保护。

## 二、步骤 0：后端「新建＋新建」并发能力验证（核心已完成）

> 目的：先证明两个后端本身支持多 session 并发，再动手改工作台（P0-2 步骤 0）。

- 验证脚本：`test_concurrency_backend.py`（两线程启动两个独立 CLI 进程，探针 `sleep` 后按单调时钟区间算重叠，阈值 2s）。
- **Codex「新建＋新建」**：3 次成功重叠（8.8s / 15.6s / 20.0s），另有 1 次 `invalid`（模型提前报告部分 stdout，只有 PROBE_START，属后端行为）。
- **DSH「新建＋新建」**：3/3 pass（19.4s / 19.7s / 18.9s）。
- 关键经验：DSH 探针必须放在项目目录子目录（`step1/.conc-dsh-*`），`tempfile.mkdtemp()` 的临时目录会被 DSH 沙箱 workspace-write 拒读；桌面端走 Electron + `ELECTRON_RUN_AS_NODE=1` 适配。
- **结论**：两个后端均支持独立 session 真实重叠执行、两侧成功、session 不同、无 writer 冲突。
- **仍未验证**：恢复路径（恢复＋恢复、新建＋恢复）与取消隔离；这两项不仅涉及调度器，也涉及后端恢复语义和子进程行为，留到工作台端到端验收。

## 三、各方意见与审核过程（按时间线）

### 3.1 Codex 初始方案（步骤 1–6 + 验收矩阵）
- 步骤 1 数据/实例/资源身份 → 2 统一调度与容量 → 3 执行域与 session 保护 → 4 取消/超时/重启 → 5 工作区隔离 → 6 诊断与端到端验收。
- 建议初始容量：全局运行 4、每 Agent 2、全局等待 32、每执行域等待 8。
- 实施顺序：步骤 1 → 2（串行配置）→ 3 → 4 → 5 → 开放跨群并发 → 6；诊断贯穿全程。

### 3.2 DeepSeek 审核（6 项 + A–D 补充）
- 6 项表态：容量需补配置校验与语义；工作区需补 8.3 短路径、只读判定只来自工具权限约束；Windows 进程树（Job Object）需前置验证；取消后 session 需按后端实测；实例锁需覆盖所有写库入口；job 内串行符合边界。
- 4 个补充点：A 多资源获取固定顺序防死锁（原子预留）；B 取消/Popen 临界区原子复查；C 迁移幂等落点；D 恢复组合由工作台端到端验证。

### 3.3 Codex 定稿回应
- 容量：任一队列满即拒绝，多收件人原子接受或拒绝，等待数不含运行数。
- 工作区：初版共享/重叠工作区一律独占，只读共享延期。
- 进程树：Job Object 保持候选机制，需验证 Electron 链、嵌套 Job、子进程创建竞争、服务退出后行为。
- 取消后 session：保留 ID 并标记 `needs_validation`，确认进程树退出后才允许受控恢复验证，不静默新建。
- 实例锁：一个数据库只允许一个执行服务；桥接正常消息写入用 SQLite 事务，迁移另行协调。
- 原子预留：资源不足全部不占用，不持有部分资源等待；取消登记与启动许可同一原子判定边界。
- 迁移：新增 `workbench_schema_migrations`，迁移与版本记录同事务提交。

### 3.4 Agent 扩展性要求（Codex 重新检查，提升为一等要求）
- 指出现有硬编码：`web_chat.py:59` Agent 列表写死、`143/1968` 二选一分发、页面提及/名称写死、session 管理 Codex 表 + DSH key 并存、`agent_registry.py:22/81` 默认身份与 adapter 固定。
- 提出：分离 Agent 身份与后端适配器；两种新增成本目标（已有 adapter 的新 Agent 只改配置；全新后端只加适配模块）；统一 adapter 契约；数据与页面全部由注册表驱动；5 条扩展验收。

### 3.5 DeepSeek 修复方案设计（含 adapter 契约）
- 架构分层：`Agent 注册（身份）→ Adapter 注册表 → 统一执行层`；`agent_id` 不再直接决定后端类型。
- 新模块：`execution_store / service_instance / workspace_resources / execution_scheduler / agent_adapters`。
- Adapter 负责：CLI 可用性检查、命令/环境/stdin、session/工具事件/最终回复/错误解析、能力声明。通用执行层负责：执行域、队列、容量、进程启动、取消/超时/清理/退出确认、session 代次落库、诊断。

### 3.6 Codex 审核设计 + DeepSeek 逐项接受
- Codex 修订（DeepSeek 全部接受）：
  1. session 持久化归统一 session 服务，adapter 只生成后端配置身份、解释 session 事件、声明恢复要求，不各自写库。
  2. 命令契约返回统一启动描述；解析契约覆盖 stdout/stderr/退出码/结果文件（Codex 最终回复在 answer 文件，`parse_result(stdout,exit)` 不够）。
  3. 注册表是唯一身份来源（分发/提及/广播/显示名/可用性/capability 全由注册表驱动）。
  4. 影响恢复兼容性的 adapter/profile/后端配置变更也须使旧 session 失效。
  5. `cleanup_pending` 保留资源占用 + 人工处理后核验退出。
  6. 共享/重叠工作区的整次 Agent 调用持有**独占租约**（非只锁写文件瞬间）。
  7. 桥接迁移协调边界：实例锁 ≠ 迁移期写入协调。
  8. 扩展验收移出「验收」章节，场景留待后续讨论。
  9. 「全新后端只加一个类」→「新增适配模块 + 注册配置，不改通用调度/生命周期/schema/页面」；首版范围 = 本地 CLI 后端。
  10. 配置规则：唯一 `agent_id`、凭据用引用不暴露、启动时加载校验、运行中配置保持快照、多域映射同一后端 session 明确拒绝。

## 四、已敲定的方案（定稿）

### 架构分层（扩展性核心）
```
Agent 注册（id/name/aliases/adapter/enabled/配置引用/容量覆盖）
   ↓ 按 adapter 字段
Adapter 注册表（codex / dsh / 未来新后端）
   ↓ 统一契约
统一执行层（执行域、队列、容量、session、进程、取消、诊断）
```
- 多个 Agent 可复用同一 adapter，但身份/配置/session 各自独立。

### 新增模块与职责
| 模块 | 职责 |
|---|---|
| `execution_store.py` | 执行记录 `run_id`、执行事件、schema 迁移（`workbench_schema_migrations`） |
| `service_instance.py` | 数据库级服务实例锁（OS 持有的锁，非 PID 文件），先于迁移与遗留处理取得 |
| `workspace_resources.py` | 工作区身份、规范路径（含 8.3 短路径/大小写/junction/symlink）、物理身份、共享/重叠关系 |
| `execution_scheduler.py` | 统一入口、容量、排队、原子资源预留、公平调度 |
| `agent_adapters/` | Adapter 契约 + CodexAdapter + DshAdapter（未来新后端在此新增） |

### 统一 Adapter 契约（输入/输出/责任）
- **输入**：Agent 配置快照、工作区、new/resume、预期 session、执行权限策略、prompt、临时结果目录。
- **启动描述**：命令参数、环境覆盖、cwd、stdin、超时策略、结果文件位置、进程管理要求。
- **统一结果**：实际 session、最终回复、工具事件、退出/错误分类、能力不支持信息。
- **能力声明**：恢复、工具事件、权限策略、取消要求；不支持的能力明确拒绝，不自动降级。
- **禁止越权**：adapter 不自行写执行状态/session 表，不另起绕过通用进程管理的 CLI。
- 通用执行层拥有启动、超时、进程树清理、退出确认的最终责任。

### 数据模型
- 执行记录：`run_id`、来源（message/job/call）、`agent_id`、`conversation_id`、`instance_id`、状态、取消请求、起止时间、退出结果。
- session 状态：统一表，`agent_id / conversation_id / 后端配置身份 / session_id / workspace_id / generation / 状态`；迁移旧 Codex thread 表与 DSH profile session 时保留来源，不混用。
- 工作区身份、执行事件、schema 版本。

### 执行域与调度
- 提交时原子创建执行记录、分配 `run_id`；同域按提交顺序排队；跨域公平选择，繁忙域不挡其他域。
- **多资源一次性原子预留**（全局名额 + 每 Agent 名额 + 执行域 + 工作区），不足则全部不占用，不持有部分资源等待（防死锁）。
- 队列满明确拒绝；多收件人原子接受或拒绝。
- 容量 = 通用默认 + 每 Agent 覆盖；建议初始：全局 4、每 Agent 2、全局等待 32、每域等待 8。

### session 保护
- 顺序：取得执行域与资源 → 复核取消/工作区版本 → 读 session + generation → 构建命令 → 启动 CLI → 收集/确认退出 → 条件写回 → 释放资源。
- 写回校验 generation；恢复沿用预期 ID，失败明确报告；工作区切换或 adapter/profile/后端配置变更使旧 session 失效、代次递增。

### 取消、超时、重启
- 取消登记与启动许可同一原子判定边界；取消先到不启动，启动许可先到则取消必捕获随后注册的进程并清理。
- 取消先持久化再通知；排队取消不启动；运行取消/超时后有界清理；确认进程树退出后才释放资源。
- 清理未确认保留占用 + 人工兜底；重启核实进程身份（含创建时间防 PID 复用），不自动重跑有副作用任务。

### 工作区协调
- 新群独立目录/worktree；旧群保留原路径标记共享。
- 共享/重叠工作区的整次 Agent 调用持有独占租约；独立工作区可并发。
- 合并是显式操作，冲突明确暴露、原修改保留；共享 Git 元数据、迁移、发布、重启仍须协调。

### 迁移与回退
- 停止接收执行、清理活动进程、协调桥接写入 → SQLite 一致性备份（WAL 安全）→ 副本验证迁移 → 原子提交（迁移 + 版本记录同事务）→ 不支持的新 schema 拒绝启动 → 优先「新版本串行模式」回退 → 恢复旧代码前清理进程、保存升级库、用匹配备份，明确差异不自动覆盖。

### 扩展性目标与验收（一等要求）
- 两种新增成本目标：已有 adapter 的新 Agent 只加注册配置 + 凭据；全新后端只加适配模块 + 注册，不改通用调度/生命周期/schema/页面。
- 5 条扩展验收（场景留待后续）：① 第三 Agent 仅改配置即可显示/路由/执行；② 与现有 Agent 的 session/容量/取消/结果互不串线；③ 测试 adapter 仅加适配模块 + 注册即通过契约测试；④ 未知/禁用 Agent 不落入 DSH 分支；⑤ 第三 Agent 同样通过同域串行/跨域并发/取消隔离。

### 实施顺序
```
前置验证 → 步骤 1 → 步骤 2（串行配置）→ 步骤 3 → 步骤 4 → 步骤 5 → 开放跨群并发 → 步骤 6
```
诊断从步骤 1 开始记录，步骤 6 汇总；每阶段先审核变更再实施。

## 五、待再考虑 / 待验证的点

1. **Windows 进程树管理**：Job Object 能否可靠管理两后端的完整进程树（尤其 DSH 的 Electron → bin.js → pwsh → python 链、嵌套 Job、子进程创建竞争、服务退出后行为）——实施前置验证项，未确定可用。
2. **取消后 session 恢复语义**：Codex thread 与 DSH profile session 取消后的保留/失效规则需按后端实测；不假定存在无副作用的状态探测接口。
3. **桥接进程迁移协调**：桥接写消息在迁移期间的暂停/拒绝/重试机制，实例锁不能代替迁移期写入协调。
4. **实例锁与写库入口边界**（已定）：区分「唯一执行服务」与「合法消息写入者」——调度/执行状态/session 更新归执行服务（受实例锁保护）；桥接消息写入用 SQLite 事务、迁移期间单独协调；第二执行实例必须在初始化改库前被拒。需实施时梳理所有写库入口并归到对应职责。
5. **容量语义**（已定）：全局运行上限与对应 Agent 上限分别检查、同时满足（取较严）；等待数不含运行数；任一适用等待队列满即拒绝，多收件人原子接受或拒绝。数值仍为初始建议。
6. **工作区只读共享判定**：初版一律独占，只读共享延期；「只读」只能来自实际工具权限约束，不能凭 prompt 声明。
7. **验证场景**（按用户要求，方案落定后另行讨论）：跨群三组（新建/恢复组合）× 各后端 3 次、同域串行、取消隔离、容量排队、迁移回退、工作区并发/共享。

## 六、关键要点（重点提示）

1. **分离 Agent 身份与后端适配器**是扩展性的核心——`agent_id` 不得决定后端类型，分发走 `registry[agent].adapter`。
2. **资源一次性原子预留**防多资源死锁；内部多把锁时固定顺序，预留失败立即回滚。
3. **取消/Popen 临界区**必须原子复查，否则仍有漏启动竞态。
4. **先拿执行域锁，再读 session**，防首次并发重复创建；session 持久化归通用层，adapter 不各自写库。
5. **清理未确认时保留资源占用**，人工处理后仍须核验退出；不凭主进程退出认定进程树清理成功。
6. **issue #8（协作流程元阻塞）**：曾反复把「记录文档」「追加任务」卡成 `blocked`，需先最小修复，才能可靠推进 P0-2 的「设计→审核→实施→验收」多轮协作。当前状态：**核心修复与回归已通过（自动化 60 项 59 通过 1 真实 CLI 跳过 + 真实 CLI 场景 A–E 覆盖）**；保留边界：E-2 手动取消的任务卡状态与进程退出证据未完整核对，不据此宣称该边界通过。

## 七、补充审核（R1–R5）与最终定稿

> 本节记录 Codex 对本文档的只读复审，以及双方达成一致的修订口径。此轮不改变架构，属**实施必须满足的行为约束**。**P0-2 尚未实施。**

| 编号 | 问题 | 双方确认后的定稿口径 |
|---|---|---|
| R1 容量 | 第四节「队列满即拒绝」与第五节「拒绝还是等待待定」前后不一致 | 全局运行上限与对应 Agent 上限**分别检查、同时满足（取较严）**；等待数不含运行数；任一适用等待队列满即拒绝，多收件人原子接受或拒绝；数值仍为初始建议 |
| R2 实例锁 | 第五节「所有写库入口受实例锁保护」与「桥接正常写消息」冲突 | 唯一执行服务负责调度、执行状态与 session 更新；桥接消息写入使用事务，迁移期间单独协调；**第二执行实例在初始化改库前被拒**（当前启动仍先初始化、再中断遗留任务，见 `web_chat.py:2353`） |
| R3 session | 第四节仅 generation 校验，缺多域重复 session 与取消后状态 | 在 adapter 定义的后端存储/配置作用域内**拒绝跨域重复绑定**；恢复核对实际 ID；取消/异常中断后标记 `needs_validation`；**退出确认是恢复的必要条件**，恢复兼容性仍须验证；条件写回同时核对执行归属与代次 |
| R4 配置变化 | 只写「快照、复核版本」，未定不匹配怎么办 | **提交时固定配置快照及工作区版本**；启动前不匹配则停止该次启动并明确报告，不换目录、不新建 session；工作区切换协调活动调用与旧队列 |
| R5 重启恢复 | 只写核实进程身份，未定核实失败怎么办 | 接单前核对遗留执行并**重建资源占用**；确认退出才释放，仍存活或无法确认则隔离相关资源 + 人工处理路径；不能仅把 DB 状态改「中断」就放行新任务 |

### 剩余待验证 / 待定（保留）

- Windows 进程树机制（Job Object 或替代）能否可靠管理两后端完整进程树。
- 取消后的后端 session 是否允许受控恢复（恢复兼容性）。
- 桥接写库入口与迁移期间的协调方式。
- 资源隔离的具体实现。
- 恢复组合、取消隔离等工作台端到端行为。
- 验证场景（按用户要求，方案落定后另行讨论）。

### 可延后事项（确认）

- 只读共享、远程后端、同 job 内批量并发继续延期。
- 「共享 Git 元数据须协调」在涉及 worktree 实施前，先落实为明确资源规则。

---

## 八、任务 6 复盘（2026-10-27 19:10:42 +08:00）

> 本节忠实记录任务 6「重启恢复、人工解除遗留占用」的完整讨论、验证与结论，作为后续 P0-2 复盘依据。结论：**跨崩溃自动恢复机制在当前威胁模型下不成立，采纳方案 A（保守交付）**。

### 8.1 目标与 T1–T6 方案

- **目标**：重启后核对遗留执行、确认退出才释放；无法确认则隔离 + 人工解除。解决「仅改 DB 状态就放行新任务」的缺口（对应 R5）。
- **T1–T6 方案（Codex）**：启动保留隔离（各域态不凭重启释放）→ 持久化恢复证据走 schema v2→v3 正式迁移（恢复事件/审计表）→ 首版离线 CLI（inspect/verify/release）→ session 三分支 → 11 条验收。核心假设：**命名 Job + 持久化 created 记录 + `OpenJobObjectW error==2` 可作为「进程树已终止」的退出证据**。

### 8.2 二次复审 S1–S5

- **S1** 机制验证先行：Job 打开失败 ≠ 退出证明，需完整证据链 + 正反测试；
- **S2** 首版显式新建 session 入口（旧执行退出确认 + 归属匹配后归档旧候选、推进代次；失败不回退旧 ID）；
- **S3** 移除主机启动身份必过门槛；
- **S4** 证据绑定 db/run/instance/fence/启动尝试/容器，只开已有 Job，release 持锁重核验，同操作 ID 异参数拒绝，原子提交；
- **S5** 历史 unknown 首版能展示不能保证解除，v3 回退保留新库/隔离/审计。

### 8.3 验证过程与结果

| 阶段 | 脚本 | 结果 | 结论 |
|---|---|---|---|
| ① 机制验证 | `verify_job_recovery.py` | exit_code=0 | KILL_ON_JOB_CLOSE 生效、四窗口正向路径成立、error==2/5 分类成立；但控制器预持进程句柄，受控实验 ≠ 生产 |
| ② 补充验证 | `verify_task6_prereqs.py` | exit_code=1 | `job_configuration_immutable_default_token` **FAIL**：同用户受管进程可打开 Job（SET_ATTRIBUTES + WRITE_DAC）并清除 KILL_ON_JOB_CLOSE |
| ③ 生命周期 | `verify_task6_job_lifecycle.py` | exit_code=1 | `live_job_name_reopen` **FAIL**：清除 kill-on-close 后进程仍活但 `OpenJobObjectW error==2`（名称消失而进程存活） |
| ④ breakaway | `verify_task6_breakaway.py` | exit_code=1 | `breakaway_counterexample` PASS + `naive_absence_rule_safety` **FAIL**：脱离 leaf 存活时 error==2 会误放行 |

### 8.4 关键观点变更（留痕）

1. **DeepSeek 最初担忧被证实**：「清除 KILL_ON_JOB_CLOSE → Job 销毁 + 进程存活（孤儿）→ error==2 被误判为进程树已终止 → 误放行」。
2. **Codex G1 两次被证伪**：G1 曾断言「进程仍活时 Job 不销毁 → 恢复时 BLOCK_JOB_EXISTS（保守阻断）」，实测相反（名称消失而进程存活）；Codex 两次撤回并修正。
3. **最终一致**：「持久化 created + 命名 Job + error==2」作为「进程树已终止」证据，在「清除 kill-on-close」与「breakaway 脱离」两个同用户可执行篡改场景下**均不可靠，构成误放行**；且「名称查找失败」≠「内核对象销毁」。

### 8.5 技术路线评估（Codex R1–R4）与最终决策

- **R1** 权限边界（受限令牌/独立账户）：可建立真正边界，但成本高、Codex/DSH 配置/凭据/Git/SSH/工作区兼容性未验证；
- **R2** 保守交付（检查+隔离+证据展示+审计+有证据人工释放）：范围调整，不算任务 6 原目标完成；
- **R3** 独立监督进程：改善可观测性，但 breakaway 后代已脱离、监督者自身故障未解；
- **R4** 持久化退出证据：仅覆盖「退出已确认」窗口，不能补救证据提交前崩溃。

**用户决策：方案 A（R2 保守交付）**。任务 6 原「跨崩溃自动恢复闭环」目标**未完成**，降级为人工解除。限制：崩溃后无可信退出证据保持隔离不自动恢复；撤销「名称缺失作为释放依据」；人工解除分「有证据释放」与「无证据强制清除」（后者不算安全恢复闭环）；根 PID 消失/任务管理器找不到/点击确认均不补齐完整性证明；breakaway 后代/外部委托/上下文守卫/历史缺证据占用仍未解决。

### 8.6 用于复盘的关键问题

1. **威胁模型边界**：「防意外并发冲突」与「防恶意同用户篡改 Job 配置」的界限——同用户进程篡改 Job 配置到底算「恶意」还是「需纳入的意外」。
2. **退出证据的可信来源**：崩溃后如何在不依赖「可被篡改的 Job 配置」的前提下证明「进程树已终止」，是否存在可靠来源。
3. **自动恢复是否值得引入权限边界**：方案 A 牺牲了「崩溃后自动恢复」，若未来需要，受限令牌/独立账户的成本与收益如何权衡。
4. **协作调度 owner 派发 bug**：owner==final_responder 的 request_collaboration/continue_work 多次未派发（含一次 JSON 转义错误 `Invalid \escape` 导致的协议块解析失败），反复要求用户「再继续」，影响协作效率，应作为独立议题修复。

---

## 九、业界方案调研与任务 6 轻量化拆分（2026-10-27 19:30:50 +08:00）

> 本节记录对「进程/任务崩溃后遗留占用清理」的业界方案调研结论，以及据此采纳的任务 6 轻量化拆分（6A/6B）。

### 9.1 业界方案分两大类（各有根本前提）

| 类别 | 代表方案 | 根本前提 | 本项目能否满足 |
|---|---|---|---|
| **自动释放** | TTL 租约（[etcd lease](https://etcd.io/docs/v3.7/tasks/developer/how-to-create-lease/)、[ZooKeeper 临时顺序节点锁](https://developer.aliyun.com/article/1752515)、[Redis Redlock](https://redis.antirez.com/fundamental/redlock.html)、DB 租约列）、心跳超时、[文件锁 flock](https://man7.org/linux/man-pages/man2/flock.2.html)、[Windows Job KILL_ON_JOB_CLOSE](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)、[supervisord](https://docs.supervisord.org/configuration.html)、[PM2](https://pm2.keymetrics.io/docs/usage/signals-clean-restart/)、[OTP supervisor](https://www.erlang.org/doc/system/sup_princ.html)、[cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)、进程组、[pidfd/进程句柄](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers)、[watchdog](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) | 能判定「持有者已死」，或有**资源端护栏**（fencing token / 内核强制）拒绝过期持有者 | ❌ 后端 session 写入端不校验 fence、同用户可篡改 Job 配置 → **护栏缺失** |
| **状态机恢复** | DB 事务 + CAS + 幂等、状态机 reserved→running→completed/failed、saga 补偿、WAL/事务日志重放 | 只保证「记录一致」 | ✅ 可直接采用（但不证明进程已死） |

### 9.2 结论

工业界「自动释放」普遍依赖「能判定持有者已死」或「资源端护栏」，而本项目两者都缺（后端 session 写入端不校验 fence、同用户进程可篡改 Job 配置），所以**「自动释放」客观上做不了**。能落地的是「状态机保证记录一致 + 人工确认释放」。这与 §八 的方案 A、以及任务 5/4B 已有的 generation/CAS/execution_domains 状态机一致。

### 9.3 采纳 6A/6B 拆分

- **任务 6A：遗留占用展示 + 保守阻断**——启动保留非 idle 域（不凭重启释放）、只读 `inspect` 展示占用对象/阶段/阻断原因、清楚提示「退出情况未知」、复用现有表、不铺完整 schema v3、不实施 verify/release/force-clear。
- **任务 6B：遗留占用的解除与恢复使用**——后续独立设计，人工确认释放；「自动释放」需先补资源端护栏（受限令牌/后端 fence 校验），另议。

> 6A 完成 ≠ 任务 6 整体完成；6B 与「自动恢复」是后续独立议题。



