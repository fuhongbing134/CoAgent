# 调用流程互审：Codex

日期：2026-11-11。状态：互审意见，待统一稿逐项处理；本文件不代表其他两端已同意。

## 目标、方法与适用性

保持用户基线：三端各画单Agent和多Agent两类详细流程，互看讨论，最后汇总，新读者可理解。本轮全文读取 CODEX.md、DEEPSEEK_HARNESS.md、GROK.md，重点核对两端文稿并复查自稿。原稿保留，只新增本文件。当前原稿DSH实际288行，与其交接的247行不同，以本轮文件为准。

本轮重读 specs/README.md；复核沿用四项must_follow：agent-discussion-spec v1.2.0（specs/AGENT_DISCUSSION_SPEC.md，独立判断、逐点证据、异议和预算），design-decision-record-spec v1.2.0（specs/DESIGN_DECISION_RECORD_SPEC.md，目标基线和取舍留痕），document-management-spec v1.0.0（specs/DOCUMENT_MANAGEMENT_SPEC.md，原稿历史、状态与检查），task-execution-spec v1.0.0（specs/TASK_EXECUTION_SPEC.md，证据范围、充分检查和准确陈述）。四份正文沿用本会话原稿阶段全文读取，本轮未重读正文。code-review-spec v1.2.0不适用：仅文档互审，无代码修改。适用性uncertain无；读取失败无。

版本定位沿用209446f；本轮 `git diff --name-only -- '*.py' '*.js'` 无输出，仅说明受跟踪源码无未暂存差异，不覆盖暂存区或未跟踪文件。针对问题重读lifecycle、service主循环、stop、DSH解析、前端收件人、受管执行、HTTP操作和邀请解析实现。没有真实CLI、浏览器或故障注入验证，没有Mermaid渲染。

## 独立判断

三稿都可作汇总材料，但不能原样作为最终稿：最影响读者理解的是正常final路径、图与正文的时序冲突，以及将某后端行为推广到所有后端。建议统一稿保留“两类调用 + 广播对照”，将共同执行路径做一张精确时序图；异常分支用有条件状态表，避免画出实际上不存在的统一恢复入口。这个判断来自以下代码核对，不以三端赞同数量作依据。

## 逐项问题与修正建议

编号F01起供本次汇总采用；其他端自行编号可在统一稿建映射，避免覆盖其原始编号。

| 编号/程度 | 文稿位置与差异 | 源码证据及影响 | 建议处理 |
|---|---|---|---|
| F01 必须修正 | DSH:133、图三169–180、实例261/263把owner完成一律接final_ready→final；Grok:283也称Codex仍须再打一枪final，与其自身:23/155矛盾 | collaboration_lifecycle.py:180–188：无活动任务时，同身份直接completed并保存owner文本；不同身份才final_ready。service.py:227–229仅final_ready调用final。否则误报调用次数、预算与实际权限阶段 | 正常路径明确同身份直接完成；不同身份、额度/用户提前收尾另走专门final。两稿实例同步纠正 |
| F02 必须修正 | DSH:52/88/210把prompt统一stdin、最终输出统一type=final；图一也将Grok包含在此共同节点 | web_chat.py:1838–1879：Codex stdin入、文件取正文；:1977–2017：DSH stdin入、final事件取正文；grok_adapter.py:47与web_chat.py:2047：Grok prompt-file入、text/end解析。影响复制操作及理解接口 | 共同节点写“按后端传入提示并解析输出”，旁表列三端差异。采纳Grok:104–110的输入输出对照，但权限配置须按F09限定 |
| F03 必须修正 | DSH:185说只有owner邀请才授权，漏entry初始化；:188说可新增任务“或补依赖”，可能暗示可以改旧任务依赖 | service.py:59–64由entry末行邀请seed授权；lifecycle.py:167–168由owner邀请替换；protocol.py:68–92旧task定义不可变，包括depends_on | 写“entry初始化，owner后续替换授权；只能追加新任务，新任务可依赖旧任务，不能修改旧任务依赖” |
| F04 必须修正 | DSH:217将task更新和call接收都画在accept_result同一事务 | lifecycle.py:169–174写call并返回；service.py:259–261在调用接收之后另开事务更新collab_tasks。message finalizer与call接收同事务，但task终态不是该事务的一部分 | 精确图分开“接收call/message事务”与“drive标记task事务”；不能将两者画成全原子提交 |
| F05 必须修正 | DSH:230–232取消图指向terminate_process_tree；Grok:267称重启是“进程没了、状态还在库里”，其stop图也容易把stopped读成已确认退出 | web_chat.py:1564–1578实际stop_active_run→Execution.cancel，execution_runtime.py:159→ManagedProcess.cancel；web_chat.py:138–151另等退出与释放。startup interrupt_running只更新控制状态，不证明旧进程不存在；stopped视图在用户stop后即可能显示 | 用实际Execution/ManagedProcess取消链；区分取消意图、UI状态、退出证据与释放。重启写“旧执行情况需核验”，不声称进程已没了 |
| F06 必须修正 | DSH:286把正常drive闭环归于“完成事件回调再kick”，图二无ready即owner；Grok图D基本正确 | service.py:251同步call，:259更新task，:262直接continue；:263–264仅已有在途状态时返回等回调。正常串行主链不依赖前端回调 | 写正常call返回后同一drive循环继续；用户操作、锁竞争rerun和其他唤醒是另一路。无ready且无活动时才owner |
| F07 必须修正 | Grok:58声称ChatHandler.do_POST只接受本机Host | web_chat.py:2391–2417对messages入口未见此通用Host检查；collaboration_http.py:8–16只对协作action强制Host、Origin和token。监听127.0.0.1不是应用层Host校验 | 分清“服务器监听本机”与“action有同源校验”；删去messages入口已具同等Host验证的断言。不扩展成安全修复任务 |
| F08 必须修正 | Grok图B:72说“占用工作目录执行域”；DSH:94称自任务物理上“只起该Agent一个CLI” | execution_tracking.py:128–151域键是agent_id+conversation_id；工作目录是配置/快照，并非该域唯一键。自任务可能多次启动同种CLI，DSH术语不应被读成整个job仅一进程 | 改“按Agent与工作台会话预占执行域”；身份不叫一台物理机器，注明同身份可先后起多轮CLI |
| F09 必须修正/范围限定 | Grok:68“差别只在命令行和stdout取文本”过强；:110把workspace-write和final限制表述成统一效果；:275用仓库语言问题当无需查证纯问答 | web_chat.py:1825使用CODEX_SANDBOX配置；final角色才切read-only，正常owner同身份完成不设final_delivery。DSH未见相同显式工具拒绝参数。session选择、诊断、权限和输出验证也不同 | 展示静态命令实际参数和配置来源，不保证所有后端都同等禁工具；“入口只规划”是提示义务。纯问答例子用1+1等无需查证问题 |
| F10 自稿必须修正 | CODEX:147–159时序图先画占用释放再写回复，:162正文虽纠正但图仍相反；DSH时序图也漏释放闸门 | web_chat.py:1385 worker_reply在worker返回前暂存；invoke_collaboration:200后finally:209 finish_execution，再:213返回；service.py:152–160检查dispatch_allowed，之后才协议接收 | 统一图直接画CLI完成→解析/暂存→finish释放→dispatch闸门→parse→call/message接收→task更新，避免要求读者靠脚注反转箭头 |
| F11 自稿与两端图形准确性 | CODEX图一:40–49把首轮路由画到落库之后且“非completed空计划”分支容易吞协议无效；DSH图一把定向纯问答和普通广播并到无协议完成路径 | web_chat.py:2498–2501先算recipients再落库；:2145再判entry；:2183解析失败去fail_entry_message；:2200只有合法空任务直接结束 | 先路由再校验/落库，worker内标“复核路由”；entry协议失败单独画failed，不得注册job。DSH图一必须补入口解析；采纳Grok图C的主要分支 |
| F12 文档完成前检查 | DSH异常图只有孤立节点，缺waiting_user/answer/append/过期接收链；Grok末行320有孤立`)`；三稿未渲染 | actions.py:55–85用户操作，lifecycle.py:125–130过期owner回复，service.py:306澄清专用replan。现状已有支持，不能省略成未实现 | 用准确状态/操作表配连线图；统一稿清理残余符号。Mermaid至少尝试语法检查，未渲染需如实标注 |

### 已核对可采纳的部分

- 三端“同job主调度ready[0]保守串行”一致，且与service.py:239–261相符；采纳。普通广播起线程是另一执行层，不能据底层ThreadPoolExecutor推定主协作并发。
- Grok关于entry空任务直接结束、自任务再次调用collaborator、owner同final直接完成的主体说明，已核对lifecycle.py:186–188和web_chat.py:2200；采纳，按F01修其最后实例。
- DSH“逻辑角色不等于Agent身份”、成功先暂存再业务接收的核心判断，已核对web_chat.py:1350–1393；采纳，按F04/F10细化事务和退出顺序。
- CODEX区分失败task与活动task、旧占用不等于存活、专门final与owner直接交付，复核后仍成立；保留。但自稿图示错误不能因正文有解释而视为已解决。

## 交付、异议与下一步

当前没有需要用户作价值裁决的实质分歧：以上主要是可由代码判定的事实差异。我的保留意见是，在F01–F11影响的关键图文纠正并经终审前，不应宣布三端最终一致。该意见不妨碍统一稿阶段继续整理，也不要求本轮额外启动讨论或执行模型。

本轮只写REVIEW_CODEX.md，原稿保留。索引仍按既定统一稿任务集中维护。已实际阅读三稿并逐项核对有争议源码；仅静态检查，不代表运行验收。新手可读性建议：先给两种用户场景，再看分层图和术语表；英文状态配中文解释，源码索引后置。

预算快照5/20，本调用后预计6/20，以调度器为准。停止条件已满足：已给可操作的编号问题、证据、影响与修正，下一步由统一稿逐项处理并供两端终审。未决验证：Mermaid语法/布局、真实浏览器、CLI与故障分支仍未验证；具体部署session resolver未确认。
