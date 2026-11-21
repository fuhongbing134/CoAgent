# Web 协作联调指南

从仓库根目录运行以下命令。

## 单次 Agent 调用超时配置

Codex 和 DeepSeek Harness 默认均为 1200 秒（20 分钟）。分别读取
`AGENT_CODEX_TIMEOUT_SECONDS` 和 `AGENT_DEEPSEEK_TIMEOUT_SECONDS`，单位秒。
只接受正整数，空值或非法值回退默认值（非法值打印警告）。启动时读取，修改后需重启工作台。

```powershell
$env:AGENT_CODEX_TIMEOUT_SECONDS = "1200"
$env:AGENT_DEEPSEEK_TIMEOUT_SECONDS = "1200"
.\.venv\Scripts\python.exe .\web_chat.py
```

不设置变量也使用 20 分钟。此设置覆盖普通聊天和协作中的同类 CLI 调用；
手动停止仍可用，超时不自动回退文件。独立 live 测试的 180 秒上限不随此配置改变。
## 当前回归命令

```powershell
.\.venv\Scripts\python.exe -m unittest tests.unit.test_collaboration_foundation tests.unit.test_collaboration_entry tests.unit.test_collaboration_batches tests.unit.test_collaboration_lifecycle tests.unit.test_collaboration_actions tests.unit.test_collaboration_service -v
```
## 启动（用户本机 PowerShell）

先正常停止旧工作台，再启动当前代码。不要同时开多个工作台操作同一个数据库。

```powershell
Set-Location -LiteralPath '<project-root>'
Remove-Item Env:RUN_LIVE_AGENT_TESTS -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe .\web_chat.py
```

浏览器打开 `http://127.0.0.1:8765`，强制刷新。真实调用会产生费用。

历史实测和待验收场景见[Web 联调验收记录](../verification/WEB_COLLABORATION_ACCEPTANCE.md)。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-20 11:21:44 +08:00 | entry-3821／Web 联调指南 | 原联调记录操作章节→现行指南 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | 启动、超时和回归命令 |
