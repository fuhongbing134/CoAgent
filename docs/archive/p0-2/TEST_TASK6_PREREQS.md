# 任务 6：生产释放前提补充验证

示例说明：`<project-root>`为克隆后的项目根目录绝对路径；`<python>`为已安装Python可执行文件的完整路径。占位符不能原样执行。

当前仅交付脚本，尚未执行。先由 DeepSeek 审核，再由用户在普通 PowerShell 手动运行。不修改生产代码、数据库或工作台占用。

## 运行前

- Windows 10/11，Python 为 `<python>`，无需第三方包。
- 使用普通 PowerShell，不在 Agent 沙箱中运行；先不提权。管理员身份会改变访问权限观察，不能用提权结果代替普通用户结果。
- 脚本不 import 生产模块，也不 import 旧实验脚本；需要的 ctypes 声明为独立源码副本。
- 写入仅限新建的系统临时目录。请等审核通过后再运行。

## 复制执行

下面包含自动前置检查，路径缺失会停止：

```powershell
Set-Location -LiteralPath '<project-root>'
if (-not (Test-Path -LiteralPath '<python>')) { throw 'Python path missing' }
if (-not (Test-Path -LiteralPath '.\verify_task6_prereqs.py')) { throw 'Probe script missing' }
$task6PrereqLog = Join-Path $env:TEMP ('task6-prereqs-console-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
& '<python>' -B -u '.\verify_task6_prereqs.py' 2>&1 | Tee-Object -FilePath $task6PrereqLog
$task6PrereqExit = $LASTEXITCODE
Write-Host "Python exit code: $task6PrereqExit"
Write-Host "Console log: $task6PrereqLog"
```

通常几十秒。程序先打印 `Evidence directory: ...task6-prereqs-...`，结束打印 `RESULT`。临时目录保留 `report.json`、各场景的 intent/created/ready 记录、manager.log，以及配置写入实验的 mutation.json。

## 不要把非零退出当成需要反复重跑

| 输出 | 解释 |
|---|---|
| PASS | 该行描述的断言通过，需同时看 `kind` 和限制 |
| FAIL | 断言失败或实际前置不成立；查看详情区分实验错误和发现的问题 |
| UNKNOWN | 没有足够证据；不计作通过 |
| exit 0 | 所有场景通过且无 UNKNOWN；仍不授权生产释放 |
| exit 1 | 有 FAIL，例如实际允许 Job 内进程修改配置 |
| exit 2 | 无 FAIL，但有 UNKNOWN，例如启动上下文可靠性未证明 |

**这个版本有意保留上下文可靠性 UNKNOWN，因此正常运行也不应期待退出 0。** 同用户默认 Job 权限可能允许配置写入；若实测如此，退出 1 是发现真实前置缺口，不要把它改成 PASS。所有结果恒定 `production_verified:false`。

格式示例，非已运行结果：

```text
{"case":"no_preheld_handles_positive","status":"PASS", ...}
{"case":"job_configuration_immutable_default_token","status":"FAIL", ... "changed":true ...}
{"case":"context_production_reliability","status":"UNKNOWN", ...}
RESULT exit_code=1 production_verified=false report=...
```

## 覆盖与边界

### 1. 无预持句柄路径

`plain` 场景：管理者创建命名 Job、设置 KILL_ON_JOB_CLOSE、创建时归入挂起根进程、核验归属并持久化创建确认，再恢复线程并生成三层普通后代。控制程序只持管理者的 Popen 句柄，不 OpenProcess 受管节点，不打开目标 Job。终止管理者后，以 `close_fds=True` 启动全新恢复进程。

恢复进程不读取 node 文件、不枚举后代、不取得节点进程句柄，只检查创建记录绑定、配置、上下文观测以及 OpenJobObjectW。对象销毁可能有短暂延迟，因此有界重复启动新观察者。结果最多为 `EXPERIMENTAL_CANDIDATE_ONLY`，不授权释放。

**上下文仅是实验内近似观测，配置不可变也尚未证明，因此这一 PASS 仅验证独立恢复程序的路径，不等于完整生产前提通过。** 旧实验的原对象句柄等待仍是独立交叉证据，本实验不会假装再次独立观测了所有节点退出。

### 2. 真正执行的反向控制

- `held`：仅此负例额外打开 Job 的 QUERY/TERMINATE 句柄。管理者死亡后，查询 active>0 且恢复程序阻断；最终对这个精确 Job 请求终止并关闭句柄。
- `root_exit`：根进程主动退出，普通后代仍活；管理者以原根句柄确认根退出并查询 active>0。恢复程序因为 Job 仍存在而阻断，不使用根 PID 消失放行。这是“根退出”实测，不伪称根对象已彻底消失。
- 名称碰撞：真实第二次 CreateJobObject 调用返回已存在时，拒绝沿用。尚未分配任何业务进程。
- 权限不足：只对新建临时空 Job 设置拒绝 QUERY 的 DACL，真实获取错误码；不改文件、账户或系统 ACL。

### 3. 明确标为合成分类器反例

`kind=synthetic_classifier_counterexample_NOT_live_OS_event` 的行测试：创建记录缺失、会话不匹配、启动时间观测变化、限制配置不符、PID 复用身份冲突、根 PID 缺失但 Job 存在、权限错误、其他打开错误。

这些是**决策函数的确定性测试输入**，不是实际注销登录、系统重启或真实 PID 复用。脚本不会制造系统重启或批量创建进程迫使 PID 复用。相关真实事件统一保留 `live_reboot_logoff_pid_reuse:UNKNOWN`。PID 缺失/复用不能替代容器级退出证据。

### 4. 上下文来源

通过 ctypes 调用 `ProcessIdToSessionId` 和 `GetTickCount64`，采样三次，记录 session id、uptime，以及“墙钟减 uptime”的估算启动时间。管理者和恢复程序也独立采样并比对。

- 短时稳定性可实测，但不能证明跨系统重启或重新登录仍可可靠区分。
- session id 不是唯一登录身份或主机启动身份，数值相同不足以证明连续性。
- 墙钟变化会改变估算启动时间，计时器存在分辨率限制；脚本给出纯数学的时钟偏移反例，**不修改系统时钟**。
- JSON 中的记录可被有写权限者编辑；环境变量、用户传入的标识或 `confirmed=true` 也不是可信上下文来源。
- 因此该来源的生产可靠性保持 UNKNOWN，不能以一秒容差当生产安全规则。获取可信启动/登录身份是后续设计任务；本轮不擅自引入未核实的内部系统 API。

### 5. 配置不可变约束

在默认安全描述符、与管理者相同身份的真实 Job 内进程中：

1. 使用精确测试 Job 名申请 QUERY + SET_ATTRIBUTES。
2. 如果成功，真实调用 SetInformationJobObject 暂时去掉 KILL_ON_JOB_CLOSE，回读确认，再在 finally 中恢复原限制。
3. 仅观察是否能取得 WRITE_DAC，不实际改写 DACL。

若配置可改或 WRITE_DAC 可取得，报告 FAIL。单次 SET 拒绝不足以证明所有修改路径都不可达，因此仍为 UNKNOWN，不能自动宣布不可变性成立。

该场景不模拟管理者崩溃，管理者持有原句柄并在 finally 中显式 TerminateJobObject，即使恢复限制失败也尝试清理。**只修改本次随机临时测试 Job，不修改生产进程或 Job。** 不能把随机名称保密当访问控制；本轮不验证受限令牌、不同账户或完整隔离方案。

## 回传

请回传：

1. Python exit code 和完整控制台输出/日志。
2. Evidence directory 下的 report.json 全文。
3. 有 FAIL 的场景对应 manager.log，以及 mutation/mutation.json；如有 fatal.json 也附上。

无需生产数据库、凭据或 Agent 会话。可以遮用户名，保留场景名、错误码和状态。

## 安全和清理

只启动本脚本的 Python 测试进程，随机本地命名空间名称。不会调用 Codex/DSH，不会迁移、释放或修改工作台数据库。没有 taskkill，也不按全局进程名称批量终止。

正常和异常路径均尝试清理：控制程序通过自己创建的 Popen 句柄终止管理者；额外 Job 句柄负例只作用于本次创建的 Job；管理者有约 50 秒 watchdog，普通 worker 有约 35 秒存活上限。若中断，请等待约 90 秒并保留证据。系统级异常或权限拒绝时不承诺绝对清理成功；如仍疑似残留，回传日志，不使用批量杀 python 的命令。证据目录不会自动删除。

## 官方依据

- [Job 安全与权限](https://learn.microsoft.com/en-us/windows/win32/procthread/job-object-security-and-access-rights)
- [GetTickCount64：启动后计时与分辨率](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64)
- [ProcessIdToSessionId：取得进程会话编号](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-processidtosessionid)

结果分析后再决定下一步。实验路径通过不等于三项生产前置全部通过，更不等于任务 6 已实施。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 12:15:57 +08:00 | entry-3327／交付引用清理 | 具体本地定位→可交付摘要或通用占位符 | 用户要求；主题草稿§12；Python本机时钟 | Codex | 保留历史事实与版本范围；作者检查，待独立复核 |
