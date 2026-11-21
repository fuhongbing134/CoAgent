# 回归问题记录：status='running' 违反 CHECK 约束

## 问题现象

协作入口消息调用 DeepSeek Harness 失败，报错：

```
调用 DeepSeek Harness 失败：CHECK constraint failed: status IN ('pending', 'completed', 'failed', 'cancelled')
```

Codex 的入口路径同样受影响。报错指向本地保存逻辑，不是 DeepSeek 登录或网络问题。

## 根因

上一轮「纯问答直接终结」修复在 `update_codex_message` / `update_workbench_message` 中引入了中间态 `status='running'`，意图表示「Agent 已生成回复，但协议尚未校验/登记」。

但 `workbench_messages.status` 的 CHECK 约束只允许：

```sql
status IN ('pending', 'completed', 'failed', 'cancelled')
```

不包含 `running`。因此 agent 返回后，工作台保存回复时触发数据库约束错误，消息被标为「调用失败」。

## 修复方案

复用合法的 `pending` 暂存状态，并**单独确认调用成功**（新增 `COLLAB_CONTEXT.entry_reply_received` 标志），协议校验与落库成功后再置 `completed`。不改数据库 CHECK 约束、无需迁移。

## 修复状态：已完成

已核对 `web_chat.py` 实际改动（Codex 实施，较原清单的 6 处字面替换更进一步，做了重构）：

1. `update_codex_message` 改为委托 `update_workbench_message`，消除重复代码。
2. `update_workbench_message`：
   - validating 且收到 completed 时 `status = "pending"`（不再写非法 `running`）。
   - UPDATE 加 `AND status='pending'`，避免覆盖已变更的状态。
   - 用 `COLLAB_CONTEXT.entry_reply_received = received and cursor.rowcount == 1` 单独确认传输成功。
3. `run_agent_task`：
   - 入口前置检查改为 `row[0] != "pending"`（移除 `running`）。
   - 校验分支条件改为 `row[1] == "pending" and entry_reply_received`。
   - 最终置 `completed` 改为 `WHERE id=? AND status='pending'`。
   - 新增 `transport failed` 分支（pending 但未确认收到成功回复时明确失败）。
4. `fail_entry_message`：`status IN ('pending','completed')`（移除 `running`）。

`grep` 确认工作台消息状态已无 `running`（仅前端探针的 probe state 仍用 running，属另一概念）。

## 验证

- `py_compile web_chat.py` 通过（SYNTAX OK）。
- 自动化回归测试结果：待补充（记录时未运行完整测试套件）。

## 教训

测试的临时消息表约束必须与真实数据库一致。上一轮新增测试的临时消息表漏掉了 `status` 的 CHECK 约束，导致测试通过却未发现该回归；后续相关测试应使用与真实库相同的约束定义。
