# issue #8 待测试清单（重启后逐条执行）

示例说明：`<project-root>`为克隆后的项目根目录绝对路径。占位符不能原样执行。

> 阅读更新（2026-11-10，entry-2647）。本文保留当时重启后的执行计划；后续实际结果见[修复验证记录](ISSUE8_VERIFY_TEST.md)及[定稿方案第八节](ISSUE8_FIX_PLAN.md#八后续修复在核心修复之上)。已有结果不能因本文件标题而重新判为全部待测。取消路径的任务卡、调用状态及进程退出仍有未单独核对边界，本次未追加运行验证。

> 需**重启工作台**后再逐条执行（当前进程尚未加载本次修改）。
> 每项附可直接复制的「发送消息」与「期望结果」。
> 建议顺序：**A → B → C → D → E → F**。

## A 纯问答直接终结（核心）

- **发送**：
  ```
  @DeepSeek Harness 请用一句话说明：纯问答场景下入口应该返回什么状态？
  ```
- **期望**：
  - DeepSeek 直接回答「返回 `completed`，tasks 空数组」。
  - 消息正常完成，**不再报** `CHECK constraint failed` 或「入口协议违规」。
  - 不产生任务卡、不创建 job/task。

## B 入口单任务实际执行

- **发送**：
  ```
  @DeepSeek Harness 请把这句话「回归验证通过」写入 ISSUE8_REGRESSION_MIN.txt
  ```
- **期望**：入口只规划 → 任务实际执行 → `ISSUE8_REGRESSION_MIN.txt` 生成 → 正常 `completed`（不卡 blocked、不提前 completed）。

## C 运行中动态追加自任务（回归）

- **发送**（发送前确认目录无 `ISSUE8_SELF_TASK_RESULT2.txt`）：
  ```
  @Codex 请做一次 issue #8 协作流程验证，不修改任何程序代码。
  1. 初始计划只包含一个任务：由 Codex 给出三条「如何确认文档已成功保存」的检查建议。
  2. 初始计划不要包含记录文档任务，也不要提前创建文件。
  3. 第一个任务完成后，负责人在检查成果时，再追加一个由自己执行的「记录验证结果」任务，依赖第一个任务，并返回 continue_work。
  4. 追加任务执行时，在当前工作目录创建 ISSUE8_SELF_TASK_RESULT2.txt，内容包含 ISSUE8_SELF_TASK_OK 和三条检查建议。
  5. 文件已存在时不覆盖，明确报告。
  6. 文件创建完成后，再最终 completed 收尾，报告文件路径。
  ```
- **期望**：初始单任务 → owner 追加自任务 + `continue_work` → 新任务执行 → `ISSUE8_SELF_TASK_RESULT2.txt` 生成（含 `ISSUE8_SELF_TASK_OK` + 三条建议）→ `completed`，不 blocked、不重复执行。

## D 跨 Agent 依赖任务（回归）

- **发送**：
  ```
  @DeepSeek Harness 请让 Codex 检查 ISSUE8_FIX_PLAN.md 是否完整，Codex 给出意见后，你把验证要点写入 ISSUE8_REGRESSION_CHECK.txt
  ```
- **期望**：入口规划两任务 → 邀请 Codex → Codex 完成后 DeepSeek 写 `ISSUE8_REGRESSION_CHECK.txt` → `completed`。

## E 失败/取消如实披露（场景 4 第 3 点）

- **E-1 失败**：
  ```
  @DeepSeek Harness 请执行一条注定失败的命令（例如运行不存在的程序 nonexistent_cmd_12345），并如实报告失败：命令退出码非零、stderr 内容，不要把失败叙述为成功。
  ```
  - **期望**：agent 如实报告非零退出码 + stderr。判据是「是否如实披露失败」，不是任务状态——如实报告失败命令的任务本身可返回 `completed`（它成功执行并报告了失败），但不能把命令失败叙述为成功。
- **E-2 取消**：
  ```
  @DeepSeek Harness 请执行一条会运行较长时间的命令（例如 sleep 60），我会在运行中点「停止」。
  ```
  - **期望**：点停止后，核对实际停止状态（任务卡、调用状态与进程退出如实反映，**不强求各层状态都叫 `cancelled`**）；不强求停止后有最终答复，但不能把已取消叙述为成功。

## F 完整测试套件重跑

- **操作**（命令行，在项目目录 `<project-root>`）：
  ```
  python -m unittest test_issue8 test_collaboration_entry test_collaboration_foundation test_collaboration_lifecycle test_collaboration_actions test_collaboration_service test_collaboration_batches test_direct_answer
  ```
- **期望**：全绿；真实 CLI 相关项可标注跳过（skip），但不能有失败。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-16 12:24:09 +08:00 | entry-3327／语义返修 | 首轮替换→修正示例及历史语境 | 主题草稿§12；Python本机时钟 | Codex | 仅文档静态检查，独立复核待完成 |
| 2026-11-16 12:15:57 +08:00 | entry-3327／交付引用清理 | 具体本地定位→可交付摘要或通用占位符 | 用户要求；主题草稿§12；Python本机时钟 | Codex | 保留历史事实与版本范围；作者检查，待独立复核 |
