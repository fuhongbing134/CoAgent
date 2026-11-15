# 分支必要性：active与visited回答不同问题

版本0.6；0.1两端独立检视无阻断；教学展示已作者自查；真实案例0.4经DeepSeek静态独立检视无阻断，本次说明澄清未另行独立复查。**删visited仍是教学改写；文末REAL-GRAPH-001记录另一项真实规模边界问题，建议未落地。**

适用：依赖图允许共享依赖，要求拒绝环且避免反复展开已完成节点。不适用：输入严格为无共享树，或已在可信边界保证无环的不同算法；不能据此要求所有DFS使用两个集合，也不据分支数量判好坏。

## 来源与对照

[collaboration_protocol.py](../../collaboration_protocol.py)`validate_plan`，SHA256：`74929c29854c3444a03775df4a682eced641d60b54d4611f649faaf10ce13c44`。图先由`_validate_plan_structure`建立；两个集合初始均为空。

### 好例：项目推荐写法（函数内连续原文）

```python
    def visit(task_id):
        if task_id not in graph:
            raise ValueError("unknown dependency")
        if task_id in active:
            raise ValueError("cyclic dependency")
        if task_id in visited:
            return
        active.add(task_id)
        for dep in graph[task_id]:
            visit(dep)
        active.remove(task_id)
        visited.add(task_id)
```

`active`是当前递归路径，识别回边；`visited`是已完成节点，跳过其后续展开。二者不是同一事实的重复检查。教学问题写法仅删除`if task_id in visited: return`，其余不变：在无环共享图中仍可能得到相同合法性结果，却重复展开节点。不能将“返回值不变”推成“没有作用”。反过来，删active的环拒绝路径则影响正确性。

### 反例：教学删除visited快速返回

以下为教学改写；保留相同外层逐节点调用、graph及两个集合，不是独立脚本：

```python
    def visit(task_id):
        if task_id not in graph:
            raise ValueError("unknown dependency")
        if task_id in active:
            raise ValueError("cyclic dependency")
        active.add(task_id)
        for dep in graph[task_id]:
            visit(dep)
        active.remove(task_id)
        visited.add(task_id)
```

### 教学改进对照：优缺点与场景后果

“改进后”恢复上方项目好例中的visited快速返回，不是项目实际修复记录。

| 写法 | 优点 | 缺点／代价 | 适用条件 |
|---|---|---|---|
| 教学反例：删除快速返回 | 少一处分支；小图仍可能给出相同合法性结果，无已证实运行收益 | 已完成节点再次展开，visited仍写入却不用于跳过；共享依赖越多可能重复更多工作 | 只用于说明“结果相同不等于无作用”，不推荐用于当前依赖图 |
| 项目好例：active加visited | 区分递归路径与已完成节点，拒环并避免重复展开 | 维护集合状态；递归深度限制仍存在 | 允许共享依赖、需要检查整个依赖图 |

| 触发图／场景 | 反例机制与可能后果 | 改进后行为 | 证据及剩余限制 |
|---|---|---|---|
| a依赖b、c，二者共享d；外层遍历全部节点 | d及后续外层节点反复展开，此图10次；更大共享图可能增加校验工作 | 此图展开4次，完成节点快速返回 | 4／10已验证；没有测耗时或规模，不能据此给性能倍率或超时结论 |
| a与b互相依赖 | 此反例保留active，仍有显式拒环路径；删visited不会自动等于漏检环 | 好例也拒环 | 原版环拒绝已验证；反例保留拒环属静态依据，本次未新跑此组合 |

## 已执行证据与可复核方法

本次`python -B -`用AST抽取当前`validate_plan`，仅以返回给定图的函数替代结构构造，不运行工作台。原版和教学版分别在`active.add(task_id)`前记录节点，图为：

```python
graph = {'a': ['b', 'c'], 'b': ['d'], 'c': ['d'], 'd': []}
```

外层仍按全部节点调用visit。原版展开顺序`a,b,d,c`（4次）；删除visited返回后为`a,b,d,c,d,b,d,c,d,d`（10次）。计数是实际展开次数，不是visit调用次数；快速返回本身仍有调用。首次探测把两者混同而断言失败，改为在展开点计数后得到上述结果。它证明该小图的重复工作差异，不是性能基准或全仓收益估计。

图改为`{'a':['b'],'b':['a']}`时，原版抛`ValueError('cyclic dependency')`。本次未执行删active的递归溢出试验；该删除会失去显式环拒绝条件属静态推理。基础测试`test_invalid_dependencies_still_rejected_without_input_mutation`本次通过，包含未知依赖、自依赖、环及无输入变更检查；测试来源[test_collaboration_foundation.py](../../test_collaboration_foundation.py)，SHA256：`4ab37f6236290c322780495a1c0a76fbe33927cc82641c8afee65327cbeaa8ef`。

## 改进空间与使用边界

递归版本简洁；若任务图可达到递归深度限制，可比较迭代DFS，但本篇未测规模边界，迭代栈会增加状态维护，当前不推荐仅为“更复杂”而替换。没有某个镜像测试不自动证明分支可删、代码坏或验证不足。先问保留场景，再核测试或其他证据；本篇局部必要性分析不能替代每轮累计全量复查与回归评估，见[CODE_REVIEW_SPEC.md](../../specs/CODE_REVIEW_SPEC.md)§8.3及§9。

## REAL-GRAPH-001：递归调用栈成为隐含图深度上限


收录／观察时间：2026-11-15 22:47:31+08:00（同次快照；非首次引入时间）。Git HEAD：`61695a41274676d57dc1dee3c9393352ddca320a`；工作区有既有修改及未跟踪文档，以下来源以文件哈希绑定，不以HEAD冒充工作区内容。状态：收录时存在，正式代码未改；作者复现；0.4经DeepSeek静态独立检视无阻断，本次说明澄清已作者自查、未另行独立复查。修复时间／版本：待后续实际修复后追加。首次引入时间：未查证。修复后保留本摘录和原结论，再追加修复版本、时间及复核结果。

来源：[collaboration_protocol.py](../../collaboration_protocol.py)`validate_plan`第46—65行，源码哈希同本篇来源。真实问题是递归深度，不是visited应删除；前半篇推荐两个集合的判断仍成立。

适用：公开计划校验接收字段合法、依赖均存在的长链且未预先约定更小深度上限。不适用：经明确契约限制并在入口拒绝超深输入的系统。本次读取的结构校验未设置深度／任务数上限；未证明实际用户会提交如此大的计划。

项目问题原文（连续摘录）：

```python
        active.add(task_id)
        for dep in graph[task_id]:
            visit(dep)
        active.remove(task_id)
        visited.add(task_id)
```

1501个节点组成单链，按0到1500顺序存放，末节点无依赖；无环且引用存在。默认递归限制1000的本次解释器中，原函数抛RecursionError，无法完成合法性判断。限值依运行环境和调用栈变化，不能把1501说成固定最小失败阈值。

建议改写（未落地；替换validate_plan中的递归遍历，graph仍用原结构校验结果）：

```python
    visited, active = set(), set()
    for root in graph:
        if root in visited:
            continue
        active.add(root)
        stack = [(root, iter(graph[root]))]
        while stack:
            node, edges = stack[-1]
            dep = next(edges, None)
            if dep is None:
                stack.pop()
                active.remove(node)
                visited.add(node)
                continue
            if dep not in graph:
                raise ValueError("unknown dependency")
            if dep in active:
                raise ValueError("cyclic dependency")
            if dep not in visited:
                active.add(dep)
                stack.append((dep, iter(graph[dep])))
```

None作迭代结束标识的直接前提是迭代出的依赖项不能为None；结构边界已保证依赖是非空字符串。节点task_id亦经_text保证非空字符串，是相关结构保证，并非该哨兵安全性的另一项独立必要条件。若另一契约允许None作为依赖值，应改用独立哨兵对象。保留visited与active两个事实，使用显式栈而非提高全局递归限制；若选择公开限制图规模，须先形成产品契约及清晰错误，本次不擅自引入上限。

| 写法 | 优点 | 缺点／代价 | 条件 |
|---|---|---|---|
| 原递归 | 简洁直观，小图可正确判断 | 合法深链可耗尽Python调用栈；边界未明确表达 | 深度始终很小时可继续使用，但须明确适用范围 |
| 建议显式栈 | 深链不随依赖深度增加Python递归层，保留拒环及共享节点处理 | 维护迭代器和栈状态，更长；仍消耗内存，不是无界资源保证 | 需要支持超过递归深度的图，且依赖保持字符串契约 |

| 触发／机制 | 后果 | 建议行为与限制 |
|---|---|---|
| 1501节点无环单链，默认递归限制1000 | 原版RecursionError而非完成校验；提交可能失败，实际UI结果未复现 | 建议完成此图校验；不提供吞吐或内存基准 |
| 环／未知引用 | 改算法若丢失active或未知检查会引入回归 | 建议仍抛ValueError；只做定向内存验证，完整回归待正式实施 |

已执行：首次只抽遍历函数、用假结构构造输入复现RecursionError；后续补抽原_keys、_text、_validate_plan_structure及validate_plan，假registry仅require原样返回，1501任务字段结构合法时同样复现。建议通过同一长链和菱形图，拒绝双节点环及未知引用。无真实进程、网络或数据库调用。静态推断仅涉及潜在提交失败；首次引入时间、实际生产输入规模、性能和完整调用链未验证。

本次仅整理历史位置和格式；原判断、收录时间、证据及原检视范围保留，整理版已作者检查、未另行独立复查。前置修订依据见[文档治理草稿](../drafts/DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md)。既有未确认的秒级时间不补造。

## 变更历史

历史排序说明：已知事件时间按近到远；旧时间精度保留，版本或阶段关系仅用于定位，不推断秒数。未确认先后的旧记录保持原相对顺序；本次补录/整理时间不作为旧事件时间。

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-15 23:21:27 +08:00 | entry-3244／历史倒序整理 | 原历史展示→最新在前、文末六列 | 用户最新要求；合并前置稿见DOCUMENT_LIFECYCLE_HISTORY_DISCUSSION.md§6；时间来源Python本机时钟 | 用户；Codex整理 | 仅历史结构与排序，旧证据和批准边界保留；待两端检视 |
| 2026-11-15 23:07:05 +08:00 | entry-3234／历史整理 | 历史散落或格式不同→文末统一六列 | 用户要求及文档治理草稿 | 用户；Codex | 仅历史位置和格式；旧时间不追补，未独立复查 |
| 历史原记录（原精度保留，秒级未确认） | 既有版本 | 沿用原记录 | 2026-11-15，0.5：按D-D1／D-D2澄清既有契约及触发范围；建议代码与问题定性未改。0.4独立检视无阻断，本次文字修改已自查、未另行独立复查，处理见[主题记录](../drafts/CODE_EXAMPLE_LIBRARY_DISCUSSION.md)§13。 | entry-3209；原记录 | 原主题范围 |
| 历史原记录（原精度保留，秒级未确认） | 既有版本 | 沿用原记录 | 变更：本版首次收录REAL-GRAPH-001；此前“递归边界未测”的历史记录保留，本节增加特定规模的执行证据。 | entry-3209；原记录 | 原主题范围 |
| 历史原记录（原精度保留，秒级未确认） | 既有版本 | 沿用原记录 | 2026-11-15，entry-3209：建立两个集合用途对照；内存教学变体已比较，未运行生产文件变异、性能或实际使用试验。 | entry-3209；原记录 | 原主题范围 |
