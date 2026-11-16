# P0-2 / 4B 功能冒烟测试

示例说明：`<project-root>`为克隆后的项目根目录绝对路径；`<python>`为已安装Python可执行文件的完整路径；其余位置占位符须按所在参数替换为本机配置。占位符不能原样执行。

本次目标是检查 **4B 修改没有破坏现有功能、工作台能够正常运行**，不是保证所有功能绝无问题。完整边界、崩溃恢复、取消竞争和安全验收留到 P0-2 完成后。

## 先运行只读检查

保持工作台运行，在普通 PowerShell 中执行（不要在受限 Agent 沙箱中运行）：

```powershell
Set-Location -LiteralPath '<project-root>'
& '<python>' -B .\smoke_test.py
$LASTEXITCODE
```

脚本仅 GET 已有 agents/能力端点、解析 CLI 路径和只读查询数据库；不发消息、不启动 CLI、不执行能力探针 POST、不迁移、不重启、不修改运行库。HTTP 会产生服务的正常访问日志和内存缓存。

默认地址 `http://127.0.0.1:8765`，数据库为脚本目录的 `agent_bridge.db`（设置 `AGENT_BRIDGE_DB` 时优先使用该值）。必须确认与工作台运行库一致。自定义时：

```powershell
& '<python>' -B .\smoke_test.py --base-url http://127.0.0.1:8765 --db '<database-file>'
```

| 输出/退出码 | 含义 |
|---|---|
| PASS / 0 | 本次自动检查通过；不代表下面人工操作已通过 |
| FAIL / 1 | 存在检查失败，反馈完整输出，先不继续发测试任务 |
| UNVERIFIED（未验收）/ 2 | 信息不足、访问受限或当前有运行/保留占用；不直接等同于故障 |

有任务正在回复时，域 `running/reserved` 可以是正常现象。脚本不凭时间或单次快照判断“遗留”；等所有任务结束再执行。若仍非 idle，反馈结果，不手工清表。`pending/quiescing` 也是合法记录状态；quiescing 表示尚未确认退出，不能当完成。历史 failed/cancelled 记录本身不是脚本故障。

## 人工操作准备

以下命令**由你手动复制执行**，会通过正常 HTTP 接口产生测试消息、CLI 会话和取消记录，也会使用后端额度；不属于只读自动脚本。只在没有其他任务运行时开始，逐个后端、逐步执行，不用 `@all`，不要点 Stop 全部停止。

在同一个 PowerShell 窗口先复制准备代码。它建立唯一测试对话，不重置现有对话或 session，也不更改工作目录：

```powershell
$smokeBase = 'http://127.0.0.1:8765'
$smokeRoom = 'p02-smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
function Send-SmokeMessage([string]$Text) {
    $body = @{conversation_id=$smokeRoom; content=$Text} | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri "$smokeBase/api/messages" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
}
function Wait-SmokeReply([int]$Id) {
    for ($attempt=0; $attempt -lt 150; $attempt++) {
        $page = Invoke-RestMethod "$smokeBase/api/messages?conversation_id=$smokeRoom&limit=100"
        $reply = $page.messages | Where-Object { $_.source -eq 'workbench' -and $_.id -eq $Id }
        $active = (Invoke-RestMethod "$smokeBase/api/runs").runs | Where-Object { $_.message_id -eq $Id }
        if ($reply -and $reply.status -ne 'pending' -and -not $active) { return $reply }
        Start-Sleep -Seconds 2
    }
    throw '等待超时：先反馈状态，不重复发送或清理数据库。'
}
```

`/api/runs` 不列出已请求取消的项，因此“列表消失”不能证明进程全退。每步结束后用只读脚本核对域；取消后尤其需要确认域 idle。遇到错误或未释放占用，停止后续步骤并反馈。任务管理器只作辅助观察，不执行“结束进程树”。

## 1. new：新建对话正常回复

**目的**：确认两个后端能接受消息、创建新会话并正常结束。

**操作**：先执行 Codex，回复成功且域释放后再执行 DeepSeek：

```powershell
$sent = Send-SmokeMessage '@Codex 这是功能冒烟测试。请记住标记 P02-C-731，仅回复“Codex 冒烟正常”。不要使用工具、修改文件、联网或安排其他任务。'
Wait-SmokeReply $sent.assistant_message_ids.codex | Format-List id,status,content
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

```powershell
$sent = Send-SmokeMessage '@DeepSeek Harness 这是功能冒烟测试。请记住标记 P02-D-842，仅回复“DeepSeek 冒烟正常”。不要使用工具、修改文件、联网或安排其他任务。'
Wait-SmokeReply $sent.assistant_message_ids.'deepseek-harness' | Format-List id,status,content
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

**预期**：消息 completed、各产生一个 run，来源是 collaboration/entry（单个 @ 的实际入口），RUN 输出中 launch_mode=new，任务结束后域 idle。可能附带工作台要求的协议块，不要求逐字只有一行。

**反馈**：后端、测试对话 `$smokeRoom`、消息 ID、状态、报错及脚本输出。若返回协作计划而不是直接回答，先停止，避免自动扩展任务。

## 2. resume：同一对话继续回复

**目的**：确认续聊不报错，每次只有一个新 run。

**操作**：仍在同一窗口、同一 `$smokeRoom`，逐条执行：

```powershell
$sent = Send-SmokeMessage '@Codex 请回复上一轮让你记住的测试标记。不要使用工具或安排任务。'
Wait-SmokeReply $sent.assistant_message_ids.codex | Format-List id,status,content
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

```powershell
$sent = Send-SmokeMessage '@DeepSeek Harness 请回复上一轮让你记住的测试标记。不要使用工具或安排任务。'
Wait-SmokeReply $sent.assistant_message_ids.'deepseek-harness' | Format-List id,status,content
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

**预期**：两轮 completed，launch_mode=resume，每条请求只新增一个 run，域释放。能回忆标记仅表示用户可见的上下文连续；工作台会注入消息历史，因此不能单凭记忆证明 CLI 恢复 ID 正确，后者留待任务 5 验收。

**反馈**：状态、标记是否正确、RUN 的 launch_mode、是否重复回复或出现忙拒绝。

## 3. 取消：停止当前测试消息

**目的**：确认停止操作可用、页面/接口可继续使用，不制造崩溃或并发竞争。

**操作**：先只测试 Codex。发送较长纯文本任务，然后取消这一条消息：

```powershell
$sent = Send-SmokeMessage '@Codex 请直接写一篇约2000字的通用时间管理说明。仅生成文字，不调用工具、不改文件、不联网、不分配任务。'
$smokeId = $sent.assistant_message_ids.codex
Start-Sleep -Seconds 2
$stopBody = @{message_id=$smokeId} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$smokeBase/api/stop" -ContentType 'application/json' -Body $stopBody
Wait-SmokeReply $smokeId | Format-List id,status,content
Start-Sleep -Seconds 12
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

确认域 idle 后，再测试 DeepSeek：

```powershell
$sent = Send-SmokeMessage '@DeepSeek Harness 请直接写一篇约2000字的通用时间管理说明。仅生成文字，不调用工具、不改文件、不联网、不分配任务。'
$smokeId = $sent.assistant_message_ids.'deepseek-harness'
Start-Sleep -Seconds 2
$stopBody = @{message_id=$smokeId} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$smokeBase/api/stop" -ContentType 'application/json' -Body $stopBody
Wait-SmokeReply $smokeId | Format-List id,status,content
Start-Sleep -Seconds 12
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

**预期**：取消请求被接受；尚未结束的消息显示 cancelled，最终域 idle。若任务在取消前已完成，completed 或“没有运行任务可停”不能算取消故障，但该后端取消项记“未验收”，不要循环重试。`not_started` 分支的 run 可以 exit_confirmed=0 且域 idle；不能仅凭此字段判失败。

**反馈**：停止接口输出、消息状态、只读脚本输出。quiescing/未释放时不要继续发送或手动改库。返回“已请求停止”不等于已确认退出。

## 4. 多层后代：只观察是否有明显残留

**目的**：发现取消后明显持续运行的异常，不专门制造进程树。

**操作**：用 `Ctrl+Shift+Esc` 打开任务管理器，在前一步运行及取消后观察工作台相关 CLI；与运行前比较。再次执行：

```powershell
& '<python>' -B .\smoke_test.py --conversation $smokeRoom
```

**预期**：没有可明确归属于本次测试、且持续运行的 CLI；域最终 idle。不要把正常桌面端、工作台 Python、其他 Codex 会话当作孤儿进程。

**反馈**：只反馈可识别的 PID、名称、持续现象；无法归属就记“未验收”。没看见残留不能证明所有多层后代退出，此处不作安全保证。

## 5. 容器外依赖：记录异常，完整验证后置

**目的**：检查普通聊天是否出现意料外的文件修改、后台任务或错误。

**操作**：检查本次回复、工作台错误提示及当前工作目录是否有意外变化；可用以下只读查询查看测试消息：

```powershell
Invoke-RestMethod "$smokeBase/api/messages?conversation_id=$smokeRoom&limit=100" | ConvertTo-Json -Depth 8
```

**预期**：本次纯文本任务没有要求任何写文件或后台操作。观察无异常只算功能冒烟正常，**不能证明不存在 WMI/服务/daemon 委托**；该安全项本次明确记“未验收，留待完整测试”。不用任务管理器观察结果宣称完整进程容器验收通过。

**反馈**：意外变化、报错、可识别的后台进程现象；没有证据时写“未观察到异常，容器外依赖未验收”。

## 反馈模板

```text
测试对话：
自动脚本：退出码 / PASS、FAIL、UNVERIFIED 汇总
Codex：new / resume / 取消（通过、失败或未验收）
DeepSeek：new / resume / 取消（通过、失败或未验收）
多层后代：观察结果 / 未验收
容器外依赖：未验收；是否观察到异常
异常消息 ID、报错和脚本输出：
```

测试消息留作正常历史记录；本次不清库、不删 session、不重启工作台。不要把本冒烟结果外推为 P0-2 全部完成或完整安全验收。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 12:15:57 +08:00 | entry-3327／交付引用清理 | 具体本地定位→可交付摘要或通用占位符 | 用户要求；主题草稿§12；Python本机时钟 | Codex | 保留历史事实与版本范围；作者检查，待独立复核 |
