# 异常与清理：请求成功不是退出证据

版本0.4；0.1两端独立检视无阻断；本版补齐教学反例代码和场景后果表，作者自查、未再次独立复查。**吞异常返回True是教学反例，不是项目现存缺陷；本篇没有启动真实子进程。**

适用：Windows Job清理中只有确认进程退出且Job活动数为零才可声称confirmed；查询失败须保留unknown。不适用：契约只承诺“已提交终止请求”的API；也不覆盖通过服务、WMI或外部守护进程委托的工作。模块自身注明尚未接入生产，不能把案例说成生产效果已验证。

## 项目来源及对照

[managed_process.py](../../managed_process.py)`ManagedProcess._empty`、`_cleanup`及`run`收尾，SHA256：`507e96efc4832e2ae6a8361841ff5b1e87a5a8cfbcb03241fd63af9790e1b159`。

### 好例：项目推荐写法（原文）

```python
    def _empty(self):
        return self._poll() is not None and self._active() == 0

    def _cleanup(self, seconds):
        # Successful TerminateJobObject is only a request, never exit evidence.
        try:
            self.api.check(self.api.k.TerminateJobObject(self.job_handle, 1))
            deadline = time.monotonic() + seconds
            while True:
                if self._empty():
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except OSError:
            return False
```

教学问题写法：仅把`except OSError`中的`return False`改为`return True`。这将“无法查询”伪装成“确认退出”。静态核对`run`收尾：已有进程且证据尚非confirmed时，以`_cleanup`真假选择confirmed或unknown。`ProcessResult.dispatch_allowed`只接受not_started或confirmed；因此伪成功可能错误放开后续调度，最终是否保留confirmed还取决于后续轮询是否另报错，不能声称所有异常都会错误放行。

### 反例：教学吞掉查询失败

以下为教学改写，沿用上方_empty及相同对象、time上下文；只改异常返回值：

```python
    def _cleanup(self, seconds):
        try:
            self.api.check(self.api.k.TerminateJobObject(self.job_handle, 1))
            deadline = time.monotonic() + seconds
            while True:
                if self._empty():
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except OSError:
            return True  # 教学错误：查询失败被当作确认成功
```

### 教学改进对照：优缺点与场景后果

“改进后”回到项目好例的异常返回False，不表示项目发生过此事故。

| 写法 | 优点 | 缺点／代价 | 适用条件 |
|---|---|---|---|
| 教学反例：异常返回True | 在本例退出证据契约下无明确收益 | 将未知状态变为伪成功，调用者可能错误认为可继续 | 不能用于承诺确认退出的本例；仅供辨析 |
| 项目好例：异常返回False | 保留不确定性，使调用者可保持unknown | 可能保守阻止后续调度；布尔值不细分失败原因 | OS查询可能失败且后续工作依赖退出证据 |

| 触发状态／场景 | 反例机制与可能后果 | 改进后行为 | 证据及剩余限制 |
|---|---|---|---|
| 终止请求成功，_empty查询抛OSError | 反例返回True；run收尾可能据此设confirmed，若后续轮询未另报错，可能允许后续工作与尚未确认退出的工作重叠 | 返回False，调用者保留unknown，禁止按confirmed放行 | 函数False／True差异已隔离验证；重叠是条件性风险，未做真实Job事故复现；后续_poll报错仍会纠正为unknown |
| 测试整体mock _cleanup=False | 跳过整个函数，不能据其断言声称内部except覆盖 | 用该测试只判断调用者处理False；内部异常另需对应证据 | 此为证据使用对照，两版代码都不能让整体mock自动覆盖内部路径 |

## 已执行证据与mock范围

本次从AST抽取`_cleanup`内存编译，使用假API：终止请求返回1、check原样返回，`_empty`抛`OSError('injected')`，不调用任何Windows API。原版返回False，教学版返回True，断言通过；未运行整个run，不证明Windows真实退出、句柄关闭或子孙清理均正确。

静态核对[test_managed_process.py](../../test_managed_process.py)，SHA256：`719281edf3c9bcc68b570bc648379eec9fc19f16cddffeeb0a48188c89a3368a`。`test_query_failure_cannot_claim_confirmed`注入`_active`查询错误，断言unknown且禁止调度；`test_cleanup_failure_retains_unknown_even_if_close_kills_child`整体mock `_cleanup=False`，断言调用者保留unknown。后者能验证调用者处理False，却不能证明被替换函数内部except已执行。两项本轮仅阅读，未运行真实进程测试，不将历史测试名当成本轮通过证据。

## 改进空间与代价

布尔返回足以供当前调用者保守处理；结构化失败原因可改善诊断，但需改接口及调用点，本次未证明收益足以抵偿成本。不能因入口参数已校验而删掉OS失败处理或清理；OS状态可能变化。更广的清理正确性、真实Job故障与性能均待专项验证；本篇没有据缺少某测试判真实代码缺陷。参考[CODE_WRITING_SPEC.md](../../specs/CODE_WRITING_SPEC.md)§5.2及[CODE_REVIEW_SPEC.md](../../specs/CODE_REVIEW_SPEC.md)§8.3。

本次仅整理历史位置和格式；原判断、收录时间、证据及原检视范围保留，整理版已作者检查、未另行独立复查。前置修订依据见[文档治理草稿](../drafts/DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md)。既有未确认的秒级时间不补造。

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-15 23:07:05 +08:00 | entry-3234／历史整理 | 历史散落或格式不同→文末统一六列 | 用户要求及文档治理草稿 | 用户；Codex | 仅历史位置和格式；旧时间不追补，未独立复查 |
| 历史原记录（原精度保留，秒级未确认） | 既有版本 | 沿用原记录 | 2026-11-15，entry-3209：首次建立清理反例与mock范围辨析；隔离片段差异已验证，真实进程与实际使用效果未验证。 | entry-3209；原记录 | 原主题范围 |
