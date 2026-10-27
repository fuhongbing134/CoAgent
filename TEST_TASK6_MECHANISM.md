# 任务 6：Windows Job 跨崩溃机制验证

状态：仅提供脚本，尚未在目标环境运行。此实验不迁移数据库、不解除工作台占用，也不启动 Codex/DSH 会话。

## 1. 准备

- 使用 Windows 10/11 的普通 PowerShell；不要在 DSH 沙箱里运行。
- Python 路径为 `E:\Python\python.exe`，不需要安装依赖。
- 默认不需要管理员权限。请先用普通权限运行；权限异常保留原输出，不要先提权重跑掩盖差异。
- 可以保持工作台原状；脚本不导入项目模块、不连接 `agent_bridge.db`、不停止服务。
- 请等 DeepSeek 完成脚本审核后再运行。

## 2. 复制运行

打开普通 PowerShell，完整复制以下命令。预计几十秒，异常超时可能更久：

```powershell
Set-Location -LiteralPath 'C:\Users\FHB\Documents\Codex\agent-lab\step1'
Test-Path -LiteralPath 'E:\Python\python.exe'
Test-Path -LiteralPath '.\verify_job_recovery.py'
$task6Log = Join-Path $env:TEMP ('task6-console-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
& 'E:\Python\python.exe' -B -u '.\verify_job_recovery.py' 2>&1 | Tee-Object -FilePath $task6Log
$task6Exit = $LASTEXITCODE
Write-Host "Python exit code: $task6Exit"
Write-Host "Console log: $task6Log"
```

两个 `Test-Path` 应为 `True`。如果为 `False`，先停止并回传缺失路径；不要执行后续命令。

脚本开头打印 `Evidence directory: C:\...\Temp\task6-job-probe-...`。该唯一目录保留 `report.json`、每个场景的 intent/created/ready 记录及 `manager.log`，方便审核。只在该临时目录写实验数据。

## 3. 怎样看输出

下面仅为格式示例，不是已取得的结果：

```text
{"case":"existing_accessible","status":"PASS","observation":{"opened":true,"error":0}}
{"case":"destroyed_vs_never_created","status":"PASS", ... "same_error":true}
{"case":"access_denied","status":"PASS","observation":{"opened":false,"error":5}}
{"case":"before_registration","status":"UNKNOWN","expected_unknown":true, ...}
{"case":"running_unique","status":"PASS", ...}
{"case":"extra_handle_prevents_last_close","status":"PASS", ...}
RESULT exit_code=0 production_verified=false report=C:\...\report.json
```

| 输出/退出码 | 含义 |
|---|---|
| `PASS` | 该受控场景的断言满足；不是生产恢复授权 |
| `UNKNOWN` + `expected_unknown:true` | 特意验证的证据缺失窗口，正确保持不确定；允许实验退出 0 |
| 其他 `UNKNOWN` / 退出 2 | 必需观察未获得，例如权限实验未复现，不能写成全部通过 |
| `FAIL` / 退出 1 | 断言或实验环境失败，检查对应日志 |
| 退出 0 | 所有已定义断言通过，包含预期 UNKNOWN；仍不代表任务 6 完成 |

重点回传 `destroyed_vs_never_created` 的两个真实错误码。若两者同为 2，只能说明错误码本身无法区分；不能因此解除占用。打开失败立即捕获 `GetLastError`，成功则记录 0。

## 4. 实际覆盖范围

脚本复制所需 ctypes 结构/常量，独立调用 Windows API。测试名称使用随机 `Local\Task6Probe_...`，不会打开或终止工作台原有 Job。

| 场景 | 操作及预期边界 |
|---|---|
| 存在且可访问 | 创建空 Job，真实 Open/Close；成功码 0 |
| 已销毁 vs 从未创建 | 关闭空 Job 最后句柄，与另一个未创建随机名称比较错误码 |
| 权限不足 | 仅对临时测试 Job 设置拒绝 QUERY 的 DACL，真实调用 Open；期望 5，未复现记 UNKNOWN，不更改目录或系统 ACL |
| 错误名称/作用域限制 | 查询不同随机目标，展示错误目标可能表现为不存在；**不冒充跨登录会话的作用域验证** |
| `before_create` | 仅启动意图已落盘，终止管理者；恢复判断 UNKNOWN |
| `before_registration` | Job 与挂起根进程已创建，但 created 记录未提交；恢复判断 UNKNOWN |
| `before_resume` | 已落盘创建身份和归属，尚未恢复根线程；检查管理者终止后的根进程与 Job |
| `running_unique` | 三层进程均确认 Job 归属；管理者是唯一 Job 句柄持有者，强制终止管理者后等待全部已知进程退出 |
| `running` | 控制程序额外持有 Job 句柄，终止管理者后检查三层进程仍活；关闭额外句柄后再检查退出，防误认为管理者死亡必然触发最后关闭 |
| `empty` | 等待三层进程自然退出、Job active=0，但不提交退出证据；终止管理者后由新观察进程核验 |

创建使用 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 在创建时归入 Job，根线程挂起；不先运行再补归入。管理者验证后代归属。所有句柄不继承，普通后代不启用 breakaway。

进程身份包含 PID 和创建时间。控制程序在崩溃前持有已知进程句柄作为独立观察依据；**这些是进程句柄，不是 Job 句柄**。崩溃后新观察进程不继承它们，通过持久化记录重新查询；其结论还必须与控制程序等待原句柄退出的观察相符。

`controlled_candidate:true` 仅表示“匹配的创建记录 + 打开不存在码 + 已知进程状态”在这个受控树上满足候选条件。脚本始终输出 `production_verified:false`，不会执行释放事务。已知三层树不能代替任意生产后代的完整性证明；额外句柄、名称重用、跨登录会话/系统重启、恶意篡改与电源故障持久性也没有因此获得证明。`fsync`+原子替换仅用于本轮管理进程崩溃实验，不宣称数据库级掉电保证。

跨登录会话的 `Local/Global` 定位尚未实测，报告明确记录 `cross_logon_namespace_tested:false`。若生产方案依赖跨会话恢复，必须另做验证；本轮不能用错误名称用例代替它。

## 5. 回传什么

请发回：

1. `Python exit code` 和完整控制台输出（或 `$task6Log` 对应文本）。
2. Evidence directory 中的 `report.json` 全文。
3. 若有 FAIL/UNKNOWN，再附对应场景的 `manager.log`；若存在 `fatal.json`，也一并提供。

不需要发送生产数据库、凭据或真实 Agent 会话内容。输出可能包含本机路径、测试 PID 和系统信息，分享前可遮盖用户名，但保留错误码、状态与场景名。

## 6. 清理与中断

正常结束会关闭测试句柄，Job 最后关闭触发清理；控制程序只通过自己创建的 `Popen` 句柄终止管理者，不按进程名称或全局 PID 列表批量杀进程。临时证据目录刻意保留，不自动递归删除。

如按 Ctrl+C 或控制程序异常退出，测试管理者最长等待约 60 秒后关闭 Job；普通测试进程自身也有约 45 秒存活上限。请等待约 90 秒。若仍疑似残留，保留日志并反馈，不执行 `taskkill /IM python.exe` 等批量命令。系统级终止/权限异常时不能承诺绝对自动清理成功。

## 7. 后续判断

先审核实测输出及官方语义是否构成完整证据链，再决定任务 6 的具体机制。没有可靠正向路径则任务 6 未完成，重新讨论技术选择；不把“检查与隔离”当完成，也不自动迁移或切换生产。

官方依据：

- [CreateJobObjectW：创建/打开及生命周期](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw)
- [OpenJobObjectW：访问权限、名称作用域及错误信息](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-openjobobjectw)
