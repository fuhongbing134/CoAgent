# 受控并发性能工具

版本：0.3。状态：隔离运行及覆盖边界见[阶段报告](../../docs/drafts/CONCURRENCY_LOAD_TEST_REPORT.md)。需求与设计以[重启基线](../../docs/drafts/TEST_RESTART_BASELINE.md)为源。

默认没有运行模式。以下为各模式的独立执行入口：

```powershell
python -B tools/performance/runner.py --run smoke --output <新的结果文件>
python -B tools/performance/runner.py --run collaboration-smoke --output <新的协作结果文件>
python -B tools/performance/runner.py --run controlled --output <新的结果文件>
```

每次必须全新Python进程；工具创建新隔离根、库和工作区，环境白名单在产品导入前生效，只允许本机HTTP，三后端都由绝对Python fixture替代，无真实模型回退。输出文件必须不存在。临时根保留，不自动删除；清理只取消自己实例持有的运行并等待退出确认，失败退出非零，禁止按名称杀进程。只应在Windows使用真实Job验证。

`smoke`只使用试运行配额6次，覆盖三adapter各两次（单并发及三并发），不是new/resume验收。完整`controlled`也包含同一6次试运行。分开执行的smoke属于额外工具验证，不可与完整批次混合统计或隐瞒调用数；历史 smoke 已完成，协作 smoke 的修订与实测见阶段报告。`collaboration-smoke`独立新实例，最多1请求/5启动/1同时进程/60秒，审计固定五调用及4个receipt，不自动并入正式批次。完整上限450外部预约/470进程启动/6同时进程/12分钟，无重试；unknown及其探针明确跳过，实际不必凑满上限。控制命令和10Hz内终态轮询另计，不启动worker。

实现映射：core.py负责预约、预算、分类；fixture.py负责三协议输出；harness.py负责导入隔离及原命令边界包装；runner.py负责实际HTTP场景、对账和报告；resources.py负责Windows只读采样；test_pure.py只运行纯逻辑及现有协议解析。原worker、begin/finish、Execution与ManagedProcess保持真实实现。entry为合法continue_work与同owner两任务；owner completed；每job5次启动硬上限及lifecycle round_limit=5共同限额。

计时限制：服务事件同进程perf_counter_ns；进程before_resume是可观测恢复边界，不是内核精确启动；result_observed是结果返回上界，无精确根退出时间。协议receipt返回是提交已可知的上界；entry精确提交未采集。轮询终态含观测延迟。闭环及开放组统一60秒安全截止；分母采用首个实际发送至全批观察结束的墙钟窗口（安全截止截断），失败、拒绝及排空均在窗口中，另报发送跨度及排空。无发送不可计算。classification保留，cutoff_classification、final_classification与late分开，截止后结果不回写截止态，不称稳态容量。到达预约只用750ms单并发周期D，24截断和6在途丢弃显式记账。

资源保护覆盖同进程驱动+服务及已知fixture根（fixture不创建后代）；不是排除驱动后的纯服务RSS/CPU。每500ms记录累计CPU、RSS、可用内存、DB/WAL和查询缺口；阈值采用合计量保守停止。无法据此单独归因工作台资源。未实现独立工作区变体，当前只用同一新只读fixture工作区；真实CLI权限、持久session、浏览器路径、生产默认统一session验证均未覆盖。

实际作者检查：`python -B tools/performance/test_pure.py`，18项通过；六文件AST语法通过。这是早期纯检查记录，本次运行结果见阶段报告。回退为移除测试专用目录和相应文档入口，生产代码未改、无数据库迁移。

包装调用返回计数与未知进程归属分离；unknown/missing停止并保留占用。开放预约预算失败也留记录，所有future收取结果，异常不丢账。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-18 11:56:15 +08:00 | entry-3603／0.3 | 旧运行状态→同步阶段报告 | 设计形成记录及阶段报告；本机时钟 | Codex | 更新运行状态及证据范围 |
| 2026-11-16T14:09:12+08:00 | entry-3372／0.2 | 需修改→计量及异常返修待复查 | 基线§17—§18；Python本机时钟 | Codex | 18纯检查，未运行集成 |
| 2026-11-16 13:54:06+08:00 | entry-3372／0.1 | 无→测试专用工具待检视 | 基线§15及0.3独立复查；Python本机时钟 | Codex | 作者纯逻辑检查，未运行集成，无UI改动 |
