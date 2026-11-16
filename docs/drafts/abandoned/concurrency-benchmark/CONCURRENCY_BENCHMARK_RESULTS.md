# 工作台并发压测实施与证据

示例说明：`<output-dir>`为该命令创建的全新输出目录，不能指向历史证据。占位符不能原样执行。

> 当前状态：废案（2026-11-16 11:25:17 +08:00，entry-3310用户决定）。旧压测方案、脚本和结果不作为新基线；原执行指令与“运行中／下一步执行”均为历史时点表述，已失效，不构成恢复运行授权。旧正文及未完成项保留，不追认完整验收。
> 原路径：`docs/design/CONCURRENCY_BENCHMARK_RESULTS.md`。归档入口：[README.md](README.md)；讨论及迁移依据：[BENCHMARK_RESTART_LESSONS_DISCUSSION.md](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)。归档文档及工具见入口；旧实验未重新验证。

## 历史原文（保留当时语境）

文档状态：执行记录，待独立检视。运行状态：原主矩阵执行中为历史记录；entry-3284本轮无模型预检通过，正式主矩阵未运行，完整验收尚未完成。

依据：[已决定设计](CONCURRENCY_BENCHMARK_DESIGN.md)、entry-3049 用户先讨论、落文档、最后实测的授权。作者 Codex；独立实现检视与数据复核分别由既定 Grok、DeepSeek Harness 任务承担，本记录不是作者自批。

## entry-3284 本轮预检

执行前留稿见[预检讨论](CONCURRENCY_PREFLIGHT_DISCUSSION.md)。2026-11-16 10:43:45 +08:00记录，时钟来源Python本机时钟。命令与停止条件见留稿。

本轮结果（2026-11-16 10:46:34 +08:00，Python本机时钟）：4项单测通过；独立正确性2/2、冒烟正确性2/2及负载12/12通过，负载每组20、总240，audit逐消息/run对账12组240条通过。所有执行命令退出0。summary先保存summary.before-report.json再report重算，JSON一致。所有被1秒轮询观察到的父子PID均已退出，见remaining_pids=[]；短于采样间隔的进程可能未观察到，不冒称穷尽进程生命周期。资源样本128条，系统最小可用内存1711980544字节，所观察进程树RSS合计峰值216920064字节（非精确峰值），CPU为累计秒值，可据相邻样本求差；DB/WAL合计大小有采样。采样不是工具内建资源验收。

延迟仍为发送至终态观察，吞吐含排空。无真实模型调用，无正式1000样本主矩阵，C1—C17覆盖与次数、缺失性能锚点和分层指标仍未补齐。未实测超时取消树清理路径，正常退出证据不能代替该项。下一步交负责人安排正式主矩阵及覆盖缺口；无业务代码修改、未提交推送。

## 变更与运行方式

新增 [benchmark_concurrency.py](tools/benchmark_concurrency.py) 与 [test_benchmark_concurrency.py](tools/test_benchmark_concurrency.py)。未修改生产代码、数据库结构、调度或认证。架构现状与调用流程中的生产事实没有变化，无需改图；工具及证据范围在本主题记录。

```powershell
python -m unittest test_benchmark_concurrency -v
python benchmark_concurrency.py run --output <output-dir> --correctness-only
python benchmark_concurrency.py run --output <output-dir> --samples 1000 --warmup 100 --rounds 3
python benchmark_concurrency.py report --output <output-dir>
```

`run` 要求输出目录不存在，且位于项目子目录；每场景独占创建隔离库，先设置 `AGENT_BRIDGE_DB` 再导入应用。没有“指定已有数据库”的参数。拒绝链接目录和数据库硬链接。`report` 读取原始 JSONL 并更新 summary，不访问数据库。

真实 `startup_server(0)`、HTTP `/api/messages`、准入、默认 legacy session 选择、SQLite WAL、消息写入、释放和实例锁均保留。仅替换 worker 与命令发现；`ManagedProcess.run` 被设为禁止调用。实际CLI worker内部的prompt组装、输出解析、子进程及网络均不计入本基线。脚本的 `confirmed` 是注入值，不能当真实 CLI 退出证据。

## 实际环境与版本

Python 3.13.7，SQLite 3.50.4，Windows；分支 `perf/ConcurrencyTest`；不声称该分支由本端成功创建。

当前 CPU/峰值内存定时采样未实现，内存总量未采集；不能作资源容量结论。未采集环境变量全集或凭据。

## 正确性与证据边界

正式隔离批次分别在历史 run 为 0 与 10000 的新库中执行一次下列组合，不能冒充设计要求的每场景100组。

| 设计项 | 当前实际覆盖 | 限制 |
|---|---|---|
| C1 | Event 驻留 HTTP 胜者，再次同域请求409 | 非双请求 Barrier 自然竞态 |
| C2 | 现有重复调用回归 | 自然双202与注入双派发的完整实验未实现 |
| C3 | 两对话同Agent worker同时驻留，两running域 | 记录真实进入/退出事件 |
| C4/C5 | 同对话三Agent广播，三worker同时驻留，子任务全部完成 | 缺Grok分支未新增实验；广播性能双口径尚未实现 |
| C6 | 现有 lifecycle/service 回归 | 缺同job执行区间受控实验，不能以回归替代 |
| C7 | 两job同Agent，观察原锁wait/acquire/release和worker区间 | 夹具预置合法reserved调用，未走完整计划接收 |
| C8 | HTTP占用时实际invoke失败，原owner/fence不变 | 未覆盖相反先后顺序 |
| C9 | 固定桥接身份20条发送与HTTP并发，逐内容核对 | 直接桥函数，不是完整MCP传输；不保证持续重叠 |
| C10/C11/C12 | 复用已有取消、unknown、旧fence、迟到回复与幂等回归 | 未新增全部宿主层受控组合 |
| C13 | 两域先认领，第二session选择失败；not_started释放第二域，第一owner/fence不变 | tracker链，不是第二HTTP完整失败接收链；P9窗口未覆盖 |
| C14 | 真实新进程初始化同一隔离库，消息cancelled、reserved owner保留 | reserved分支；quiescing重启分支仅已有回归；无自动恢复验收 |
| C15 | 另一连接持写锁，短等待202、长等待503 | 实际BEGIN前trace握手；不声称恰好10秒返回；当前每历史量各一次 |
| C16 | 上述组合增加10000合法终态历史run | 未达到100组金丝雀预算 |
| C17 | 第二真实服务进程争同库实例锁失败 | 两个历史档各一次，非全部计划次数 |

组合结束检查 `integrity_check=ok`、`foreign_key_check=[]`、当前占用全idle。随后重启实验故意留下旧reserved域作为证据，不强清，不把其视为退出已确认。

## 负载与统计口径

主矩阵已实现：普通跨对话、客户端1/2/4/8、初始历史0/1000/10000、脚本驻留50ms、预热100与采样1000、三轮固定种子打乱。每客户端等消息终态且活动登记解除后再发下一个。100个非空且唯一的Codex绑定，历史均匀分散；历史种子为合法failed/not_started终态，并非真实用户历史。每轮实际run数仍增长。

报告延迟是 `t_send → t_observed`，终态观察包含20ms轮询、读库与释放等待开销，不能称精确终态提交时间或纯调度耗时。nearest-rank参数为p=0.50/0.95/0.99；空样本为null。成功分布条件化；失败与删失分别保留，不能平均三轮p99。吞吐字段 `cohort_success_per_second` 是本轮首个正式发送至最后观测的有限队列吞吐，含末尾排空；不是设计中另立固定窗口的吞吐实现。

当前尚未实现：跨Agent/广播/混写/协作排队性能锚点、0ms对照、绑定与消息单变量扫描、采样开关对照、全程N_domain/N_wait/客户端瞬时数、业务提交/资源释放分段指标及资源定时采样。当前主矩阵不能代替这些计划项，不能单方面视为免除。

## 已执行检查与失败记录

- 初始相关回归命令：`python -m unittest test_execution_tracking test_execution_wiring test_direct_answer test_collaboration_lifecycle test_collaboration_service test_capabilities test_grok_adaptation -q`，89项通过。包括Windows无模型进程取消，不能当作真实模型CLI。
- 补充命令：`python -m unittest test_benchmark_concurrency test_service_instance test_execution_sessions test_managed_process -q`，48项通过。与前一批89项合计137个不同测试；另行单跑的新工具3项是重复执行，不再累加。
- 新工具3项：分位数边界、拒绝已有/外部目录、删失样本重算，通过。语法检查及`git diff --check`通过；后续变更需按实际版本复查。
- `concurrency-smoke`：夹具少传session准备参数，未进入worker；已修复，属于工具错误。
- `concurrency-correctness-v3/v4`：把客户端或不充分等待当作超时保证，长锁仍202；修订为保持锁直到响应或控制器截止，校准后503。不是已复现生产安全缺陷。
- `concurrency-correctness-v6`：夹具INSERT漏写列名，后续修复；不是业务缺陷。
- `concurrency-baseline`：工具开发期间的探索数据，不用于正式基线。停止已知父进程后，显式终止尚未完成的已知子进程并留下`aborted.json`；未完成场景无可用完整样本，不计通过。没有按进程名字清理其他任务。
- PowerShell管道一次中文修订出现问号，已删除损坏新增行，随后改用补丁并回读；原正文未被该失败替换。CIM进程查询被拒，改用psutil核对本次明确PID与命令后清理。

## 真实CLI与剩余门槛

本轮真实模型CLI调用0次，token/费用unknown。既有“最多9次进程×120s”不是金额上限，也不能限制CLI内部模型轮数，故L4保持待运行。若继续，需要用户选择具体预算：只做一个指定后端至多3次进程，或三后端至多9次进程，并给可执行的模型/费用上限；不能把本轮模拟证据作为真实CLI安全结论。

作者自查不替代独立批准；未提交、推送或合入。完整设计覆盖尚未完成，必要独立检视与补齐项仍为交付门槛。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 13:13:25 +08:00 | entry-3353／G-N1与G-N2收尾 | 两方发现项→作者修正并明确版本范围 | Grok有效检视3f4a86ec1e854356b1c5776a37054e9e；[处理记录](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | Codex按负责人决定处理 | 仅文档；末次修订未另行独立复查，行为效果未验证 |
| 2026-11-16 13:03:01 +08:00 | entry-3353／N1返修 | 检视发现漏项→删除并补查 | DeepSeek调用685c355e87914e79b46f0664684c5bf0；[返修记录](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | Codex按负责人决定修正 | 作者检查，Grok检视尚待；效果未验证 |
| 2026-11-16 12:56:49 +08:00 | entry-3353／文档清理与正式化 | 旧表述→删除指定提及并关联正式记录 | 用户要求；[前置记录](../../BENCHMARK_RESTART_LESSONS_DISCUSSION.md)§13；Python本机时钟 | 用户授权；Codex实施 | 仅文档；作者静态检查，两方检视待完成 |
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 12:15:57 +08:00 | entry-3327／交付引用清理 | 具体本地定位→可交付摘要或通用占位符 | 用户要求；主题草稿§12；Python本机时钟 | Codex | 保留历史事实与版本范围；作者检查，待独立复核 |
| 2026-11-16 11:25:17 +08:00 | entry-3310／旧压测废案 | 原活动位置→废案，原执行指令失效 | entry-3310用户归档授权；前置留稿BENCHMARK_RESTART_LESSONS_DISCUSSION.md；Python本机时钟 | 用户；Codex实施 | 仅归档及引用同步，不恢复测试；教训待讨论 |
| 2026-11-16 10:46:34 +08:00 | entry-3284／预检（留稿0.2） | 预检待执行→无模型预检通过 | 本轮日志与audit；Python本机时钟 | Codex | 仅预检，完整验收未完成 |
