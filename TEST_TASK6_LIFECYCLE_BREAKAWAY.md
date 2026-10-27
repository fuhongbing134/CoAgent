# 任务 6：kill-on-close 生命周期与 breakaway 针对性验证

状态：脚本仅已编写，未执行。先由 DeepSeek 审核，再在普通 PowerShell 手动运行。两份脚本各自 standalone，不 import 生产模块、旧脚本或彼此；不访问生产数据库、不启动真实 Agent。

## 先说明要验证什么

1. `verify_task6_job_lifecycle.py`：由 Job 内进程真实清除 KILL_ON_JOB_CLOSE，**不恢复**；管理者被终止且全部测试 Job 句柄关闭后，独立观察程序同时查询原进程对象与命名 Job。记录“原进程活着时名称是否还能打开”及清理后名称何时消失。不能预先把文档的对象生命周期等同于名称查找的实际行为。
2. `verify_task6_breakaway.py`：Job 内进程真实开启 BREAKAWAY_OK，显式以 CREATE_BREAKAWAY_FROM_JOB 创建一个无后代的测试 leaf。核对它不属于目标 Job，然后终止管理者，观察“原 Job 不存在但 leaf 仍活着”能否复现。

**第二项若复现，简单的名称不存在释放规则应报告 FAIL。** 这是反例揭示规则不充分，不是把实验失败伪装成成功。

## 运行环境与命令

Windows 10/11，普通 PowerShell，`E:\Python\python.exe`。无需安装包；先不使用管理员权限，提权会改变观察结果。请等审核通过后运行。

复制以下命令顺序执行两个脚本；第二项即使第一项非零也继续，分别保存退出码：

```powershell
Set-Location -LiteralPath 'C:\Users\FHB\Documents\Codex\agent-lab\step1'
if (-not (Test-Path -LiteralPath 'E:\Python\python.exe')) { throw 'Python missing' }
if (-not (Test-Path -LiteralPath '.\verify_task6_job_lifecycle.py')) { throw 'Lifecycle script missing' }
if (-not (Test-Path -LiteralPath '.\verify_task6_breakaway.py')) { throw 'Breakaway script missing' }
$task6FocusedStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$task6LifecycleLog = Join-Path $env:TEMP ('task6-lifecycle-' + $task6FocusedStamp + '.txt')
& 'E:\Python\python.exe' -B -u '.\verify_task6_job_lifecycle.py' 2>&1 | Tee-Object -FilePath $task6LifecycleLog
$task6LifecycleExit = $LASTEXITCODE
$task6BreakawayLog = Join-Path $env:TEMP ('task6-breakaway-' + $task6FocusedStamp + '.txt')
& 'E:\Python\python.exe' -B -u '.\verify_task6_breakaway.py' 2>&1 | Tee-Object -FilePath $task6BreakawayLog
$task6BreakawayExit = $LASTEXITCODE
Write-Host "Lifecycle exit: $task6LifecycleExit ; log: $task6LifecycleLog"
Write-Host "Breakaway exit: $task6BreakawayExit ; log: $task6BreakawayLog"
```

通常几十秒，异常可能需约两分钟。分别记录两次 `Evidence directory:` 和 `RESULT`。

## 结果含义

| 场景 | 判定口径 |
|---|---|
| worker_configuration_change | 实际设置、回读的 flags 符合实验要求才 PASS；不能修改则 setup UNKNOWN |
| leaf_membership | 生命周期实验 leaf 应仍在目标 Job；breakaway 实验应不在目标 Job。外层/嵌套 Job 阻止脱离时 UNKNOWN，不伪造反例 |
| last_job_handle_closed_workers_alive | 独立观察进程确认根与 leaf 原身份仍 alive |
| live_job_name_reopen | 进程仍活且真实 Open 成功才 PASS。若返回 2，记录 FAIL 和原始状态，表示预期受到反证；不能硬改成 BLOCK_JOB_EXISTS |
| breakaway_counterexample | 原根退出、leaf 仍活、目标 Job 打开失败码 2 同时出现才 PASS（反例复现） |
| naive_absence_rule_safety | 反例复现时 FAIL，说明简单的 created+名称不存在规则会误放行 |
| guard_with_explicit_experiment_escape_evidence | 仅用本实验明确记录的脱离证据演示 BLOCK_KNOWN_ESCAPE。标记 experimental_guard_NOT_production_fix，不代表生产具备自动检测能力 |
| cleanup | 精确进程对象终止并等待成功才 PASS；清理信息不足或等待失败为 UNKNOWN |
| after_cleanup_name_absent | 有界轮询记录最终名称不存在码。不是内核销毁精确时刻的测量 |

每行含 PASS/FAIL/UNKNOWN 和 error；纯进程/逻辑断言 error 可为 null，Windows 查找失败保留真实错误码。

- 退出 0：所有本实验断言通过，没有 UNKNOWN；仍不是生产恢复授权。
- 退出 1：存在 FAIL，可能是预期的 breakaway 安全反例，也可能是生命周期预期被反证或实验错误；看 case/detail。
- 退出 2：没有 FAIL，但有 UNKNOWN，比如环境限制导致无法制造脱离。
- 所有结果恒定 `production_verified:false`。不要为了得到 0 反复重跑。

仅为格式示例，非实际结果：

```text
{"case":"live_job_name_reopen","status":"PASS", ... "opened":true ...}
{"case":"breakaway_counterexample","status":"PASS", ... "leaf":"alive" ...}
{"case":"naive_absence_rule_safety","status":"FAIL","error":2, ...}
{"case":"guard_with_explicit_experiment_escape_evidence","status":"PASS", ...}
```

## 独立观察与清理为什么可靠

- 管理者在创建时将挂起根进程归入目标 Job，核验归属、提交 created 记录后恢复线程。
- 根与 leaf 的 JSON 记录包含 PID 和创建时间。控制器在管理者崩溃前取得这两个原对象的查询/等待/终止句柄并校验创建时间；它们是**进程句柄，不是 Job 句柄**，不会阻止“最后 Job 句柄关闭”。这是本轮明确选择的独立观察/清理手段，不冒充无预持句柄实验。
- root 和 leaf 的临时 Job 查询句柄都关闭后才发布 node_ready，之后仅管理者持有测试 Job 句柄。没有第三方故意打开对象是本实验条件，不是普遍生产保证。
- 管理者终止后，新的观察进程不继承控制器句柄，重新核对身份、查询进程状态及 OpenJobObjectW。
- 清理不依赖再次成功打开 Job：控制器直接对已校验的原进程句柄调用 TerminateProcess，leaf 优先，随后有界等待退出；不按裸 PID、进程名称或全系统列表终止。
- breakaway leaf 不再创建后代，且有约 45 秒存活上限；管理者有约 50 秒 watchdog。setup 不完整时会明确报告清理 UNKNOWN，不能声称所有节点已确认退出。

本轮不修改 DACL、不创建账户、不修改系统时钟或重启系统。Job 只用随机 `Local` 测试名称，证据只写新建临时目录。正常与异常路径均尝试关闭句柄和清理；权限异常/强制中断时不能承诺绝对清理成功。中断后先等约 90 秒，保留日志反馈，不要运行批量 taskkill python 命令。证据目录刻意保留。

## 回传清单

1. 两个退出码和完整控制台日志。
2. 两个 Evidence directory 的 report.json。
3. manager.log、mutation.json、leaf.json、created.json；若 setup 或 experiment_error 异常，尤其保留这些文件。

无需生产数据库、凭据或 Agent 内容。可以遮用户名，但保留场景名、flags、错误码及进程身份一致性信息。

## 仍未解决的范围

这两个实验不会建立配置不可变性，也不修复生产恢复判定。即使实验能依据已知脱离证据拒绝，真实恢复时未必拥有可信的脱离历史；创建时记录的初始 flags 不能证明执行期间从未开启过 breakaway。上下文守卫、权限边界和最终支持范围仍需收敛，任务 6 未实施。

官方依据：[CreateJobObjectW 生命周期](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw)、[Job Objects 后代继承与 breakaway](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
