# 源码目录重组与 Web 宿主拆分设计

| 字段 | 值 |
|---|---|
| 版本 | 0.2 |
| 文档状态 | 草案 |
| 方案状态 | 推荐提案，待两方独立检视 |
| 需求基线 | [REQ-002 v1.0](../requirements/SOURCE_LAYOUT_REQUIREMENTS.md) |
| 代码基线 | `82f20f8bcd713366da20e3132dc07efc263ec776` |
| 格式参考 | [并发正确性验证设计](../design/CONCURRENCY_VERIFICATION_DESIGN.md) |

## 1. 摘要、目标与非目标

保留仓库根目录的 Web 和 MCP 启动脚本，将实现归入 `workbench/` 功能包，将测试与专用夹具归入 `tests/`。以小批次迁移，每批验证受影响的入口和行为。页面、协议、数据库结构及部署方式沿用现状。

## 2. 背景、问题与范围

REQ-002 §2–§3 是设计和验收依据。源码与测试分目录，源码按功能分级，拆开 `web_chat.py` 集中的职责。兼容要求覆盖现有启动命令和 MCP 配置、默认数据库与工作目录，以及文档指定的测试入口。测试模块名可以随文档同步迁移。entry-3706 指定以并发正确性验证设计为格式参考。

## 3. 现状、能力与约束

| 对象 | 基线与影响 |
|---|---|
| Web 与 MCP | `web_chat.py:67–69`、`agent_bridge.py:15–16` 从脚本位置推导项目根。现有路径和参数可在 [README](../../README.md) 与 [MCP 说明](../guides/MCP_SETUP.md) 核对。 |
| 首页 | `web_chat.py:2346` 的 `GET /` 返回内嵌 `PAGE`。Grok 对脚本标签和路由的静态检查显示独立的 `collaboration_ui.js` 不在当前 HTTP 加载链上。保留原文件，页面模块迁移现有内嵌内容。 |
| 测试与夹具 | `test_capabilities.py:19–21` 同目录读取 `test_frontend.cjs`。夹具服务先切换工作目录再导入产品，监督器还准备隔离副本和子进程命令。迁移须同时核对导入路径、复制清单和子进程入口。 |
| 性能工具 | `tools/performance/runner.py:394` 已有仓库根导入路径处理。工具目录保持独立，更新实际引用的脚本与夹具位置。 |

## 4. 备选方案与取舍

| 候选 | 收益与代价 | 处理 |
|---|---|---|
| 迁走根脚本并改用包入口 | 根目录更整齐，但旧启动命令与 MCP 路径失效 | 排除，违反 REQ-002 |
| 保留根脚本并采用可安装的 `src/` 布局 | 可支持独立安装，需要新增打包配置与运行前提 | 暂缓，现有脚本运行方式足以满足需求 |
| 保留根脚本并采用普通功能包 | 维持运行方式，重组后仍有两个兼容入口 | 推荐。采用单一 `workbench/` 命名空间，集中定位产品代码，减少顶层包名占用 |

## 5. 方案总览与依赖方向

```text
web_chat.py / agent_bridge.py       根入口、路径锚点和依赖装配
workbench/
  web/                             HTTP 与页面
  mcp/                             MCP 工具和服务入口实现
  collaboration/                   协作和消息终态
  execution/                       执行状态、进程与取消
  storage/                         SQLite 连接、仓储与模式定义
  adapters/                        后端命令、调用与输出解析
  prompts/                         提示组装与清理
  workers/                         任务执行编排
  agents/                          注册、能力与会话配置
tests/
  unit/                            unittest 套件及同行前端测试资源
  verify/                          验证与冒烟脚本
  fixtures/                        三个并发夹具
tools/performance/                 性能工具
```

根入口装配 HTTP、MCP 和 worker 所需服务。worker 调用协作、执行、适配器、提示及存储服务。协作需要执行或存储时，通过明确依赖调用。涉及会话读写的后端运行逻辑保留现有读写顺序，调用执行和存储所需边界。存储、提示和底层进程管理不导入入口或上层编排。若迁移发现协作与 worker 互相调用，由入口注入现有回调，避免双向导入。

## 6. 详细设计

| 现有职责或来源 | 归属与约束 |
|---|---|
| `connect_workbench`、建库、查询与写入，`database_schema.py`、`schema_definition.py` | `storage/`。保留连接生命周期、事务边界与初始化顺序。既有模块先移动，避免同时合并实现。 |
| `collaboration_service`、`invoke_collaboration`、`stage_collaboration_reply`、`finalize_message` 和现有协作模块 | `collaboration/`。保留消息终态、取消与失败处理。 |
| `begin_execution`、`finish_execution`、`run_managed_process`、活动进程管理，`execution_tracking/`、`managed_process.py` | `execution/`。执行注册、取消与清理共享同一状态所有者。根据现有引用归位迁移屏障，保留其数据库与服务启动约束。 |
| 提示组装、内容清理和收件人解析 | `prompts/`。保留输入输出与解析次序。 |
| 三种 CLI 调用、命令构造与解析、`grok_adapter.py` | `adapters/`。保持参数、工作目录、环境、会话回写和错误映射。 |
| `run_agent_task`、worker 及回复／失败编排 | `workers/`。保留调用顺序、任务标识和终态责任。 |
| `ChatHandler`、`PAGE` 和 HTTP 启停 | `web/`。根脚本保留参数入口和装配。页面仍由 `GET /` 提供，继续使用现有内嵌脚本。 |
| `agent_bridge.py`、注册和配置模块 | MCP 实现归 `mcp/`，注册、能力与会话配置归 `agents/`。根脚本仍供 MCP 客户端执行。 |

两个根脚本继续从自己的实际位置确定项目根，并把路径交给实现模块。保留现有配置覆盖优先级与默认工作目录行为。测试按被测对象实际查找符号的位置更新 mock 和夹具替换点，验证替换确实命中。保留前端测试与 CJS 文件的同目录关系。具备根工作目录前提的测试和夹具优先采用模块方式启动。必须按文件路径启动的隔离夹具显式加入其实际副本的产品根，并核对监督器的复制树与导入来源。

## 7. 场景设计与验收

| 需求 | 验收证据 |
|---|---|
| R1 源码与测试分离 | 归位清单、测试用例标识和数量的前后对照、产品启动导入边检查。同步 README/MCP 说明并执行 `python -m unittest tests.unit.test_capabilities -v`。 |
| R2 按功能定位 | 文件迁移对照、代表性导入与启动链路检查，核对依赖方向及新增循环。 |
| R3 拆分集中职责 | §6 对照落实至实际文件。对比命令构造及结果、HTTP 响应、会话写回、取消和事务语义。 |
| 兼容验收 | 从仓库根和不同工作目录按既有方式启动，核对 `--port`、MCP 客户端连接、有效数据库与工作目录、`GET /` 和受影响 API。使用相同输入与配置对比迁移前后。 |
| 文档随迁 | 检查 §9 的现行相对链接和路径，从文档指定目录执行现行命令。任务 6A 的诊断入口使用隔离库，独立终端入口使用受控后端。历史命令保留所适用的版本语境。 |

## 8. 失败处理与收尾

遇到导入错误、替换未命中、路径漂移或行为差异，暂停叠加后续迁移，固定失败用例并复验。运行验证使用隔离数据库与端口，退出时核对测试服务和子进程均已清理。验证失败时保留能够定位版本、命令和差异的记录。

## 9. 影响面、引入与回滚

entry-3717 盘点了 21 份根目录 Markdown 和安装说明引用的依赖清单。各批同步处理下列引用。

| 基线文档 | 随迁处理 | 批次 |
|---|---|---|
| [README](../../README.md#L147) | 更新指定 unittest 模块、测试文件链接和 `verify_real_backends.py` 链接。CLI 可用性探测的证据按其覆盖范围记录。随模块搬迁更新产品导航，核对终端、诊断及 Web 入口。 | P1 测试、P2 导航、P3 Web |
| [MCP_SETUP](../guides/MCP_SETUP.md#L166) | 更新 unittest 模块和 `test_frontend.cjs` 定位，核对 MCP 配置、Web 启动与安装说明。 | P1 测试、P3 入口 |
| [BACKLOG](../BACKLOG.md#L112) | 保留第 112–113 行历史验收命令。同步第 126 行现行待办的测试、协议、服务和生命周期定位。 | P1 测试、P2/P3 实现 |
| [Web 联调记录](../verification/WEB_COLLABORATION_ACCEPTANCE.md#L32) | 第 34 行属于 2026-10-20 已执行检查。保留原六模块命令与结果，另列现行六模块命令并实测。复核现行启动与操作说明。 | P1 现行命令、P3 启动 |
| [任务 6A](../archive/p0-2/P0-2_TASK6A_DESIGN.md#L89) | 保留两条根目录诊断调用。随诊断实现归位验收命令与结果，保留原记录的部分实现与验收边界。 | P2、P4 |
| [AGENTS](../../AGENTS.md)、[依赖清单](../../requirements.txt) | 核对规范及待办导航、README/MCP 安装引用。 | P4 |
| Issue #8、P0-2 专题、README 候选及附加验证说明 | 保留历史命令、结果及阶段代码定位。仍在使用的操作说明按 §12 更新。专题文档归档另行评估。 | P1 测试、P2/P3 实现、P4 对照 |
| [性能工具说明](../../tools/performance/README.md) | 核对实际使用的夹具和验证指令，历史并发报告保留当时的夹具位置。 | P1/P2 |

每批把移动、调用方与相关说明作为同一回滚单元。若后续批次依赖该批，从最新依赖批次按逆序撤销，随后重跑该批的兼容检查。设计不要求库表迁移，回滚操作针对代码与文档版本，使用者数据沿用原库。

## 10. 实施计划与依赖

| 批次 | 内容 | 进入下一批的依据 |
|---|---|---|
| P1 | 记录迁移前用例和入口基线，然后移动测试、验证脚本、配对前端资源和夹具，按 §9 同步现行命令、链接及监督器路径。此时仍可导入现有产品模块。 | 新文档命令、前端测试、夹具启动及产品基本入口检查通过 |
| P2 | 按依赖成组搬迁已独立模块，逐批更新调用方及现行导航，保留终端和诊断根入口。 | 导入检查、受影响回归及相关启动和路径检查通过 |
| P3 | 分批抽取页面、提示与其他职责，按实际依赖处理运行状态和 worker 装配，最后收敛 Web 与 MCP 根入口，同步现行宿主实现定位。 | 每批检查公开入口子集及受影响行为，验证 mock 命中和状态共享 |
| P4 | 完成 REQ-002 四项验收、全量回归及 §9 文档和入口复验。 | 形成绑定版本、配置及覆盖范围的验收记录 |

## 11. 验证、验收与完成定义

草稿完成条件是 REQ-002 四项验收均有设计落点和核验办法。实施完成条件是职责归属与公开兼容可按 §7 逐项核对。验证记录分别报告用例变化、通过／失败／未执行、实际入口和配置。全量回归与入口实测分别留证。P1 至 P3 逐批检查变动的现行链接目标、章节与路径，按说明指定的解释器、工作目录及前置条件执行变动的现行命令。P4 汇总结果并复验 Web、MCP、诊断和终端入口。诊断以迁移前后等价的隔离库覆盖常规输出与筛选 JSON，核对退出状态、输出字段及只读行为。终端入口用受控后端和可退出的输入验证启动与交互。历史命令的适用版本与现行命令的执行证据各自留存。

## 12. 风险与未决问题

- 仓外是否直接导入根模块的内部符号无法由仓库引用证明。若发现已公布的符号依赖，在移除相关导出前核对兼容范围。
- 夹具对副本布局和替换位置敏感，P1 核对监督器的复制树，P3 核对实际符号查找和共享状态。
- 两份分析对顶层定义计数存在差异。综合以具名职责及调用链为依据，无须用定义数量判定拆分完成。
- P2 建议保留根目录的 `inspect_execution.py` 和 `main.py`。前者见 README 导航和任务 6A 的实际调用，后者由 README 作为独立终端脚本介绍。保留路径可减少调用方变动，内部实现仍可按职责归位。若要移动入口，先核对调用方及兼容取舍。
- P1 前确认 `ISSUE8_REMAINING_TESTS.md`、`TEST_P02_SMOKE.md` 和三份 `TEST_TASK6_*.md` 的维护用途。仍用于操作的说明随脚本更新并实测，用途未确认时保留已有命令与基线语境。P2/P3 同样核对 `P0-2_OVERVIEW.md` 的实现定位。
- 仓外书签、调用脚本、配置和固定链接尚未核实。迁移后的依赖方向和行为待实施验证。

## 13. 讨论、证据与交付边界

DeepSeek Harness 提议根入口加功能包及四阶段迁移。Grok 独立支持根入口，提出平铺功能包，并补充内嵌页面证据、隔离夹具导入、测试替换点和逐批验证。综合稿选用 `workbench/` 作为命名空间，采纳上述迁移边界。两份分析均与 REQ-002 v1.0 和代码基线对齐。0.2 依据 entry-3717 的仓内盘点补充 §7、§9–§12，已与 REQ-002 对照，待独立检视。代码实施和运行验收尚未开展。

## 14. 根目录按用途归位（续议，待用户审阅）

本节记录 entry-3743 的后续讨论。§1–§13 保留 v0.2 的评审语境，正式后继见[设计 1.0](../design/SOURCE_LAYOUT_DESIGN.md)。用户在 entry-3754 要求先看草稿。当前目标是根目录只含目录，源码和测试分开，源码内可按职责定位。复点时根目录有 34 个实有文件，其中 29 个受跟踪，5 个是忽略的数据库及辅助文件。

### 14.1 共识与保留意见

- **共同意见**：保留一级 `workbench/` 与独立的 `tests/`。四个启动脚本归入 `entrypoints/`，配置、文档和运行数据按用途归位。保留启动能力和已有数据。`src/` 决定源码位置，打包决定构建与安装方式，当前目录调整可沿用脚本启动方式。
- **目录粒度**：DeepSeek Harness 认为同一 `web/` 下的 `page.py` 和 `routes.py` 已区分职责。Grok 建议按用户示例分别建立目录，并让短小的 `transport.py` 继续留在 `web/`。Codex 推荐采用后者，入口保留原文件名。
- **根文件例外**：DeepSeek Harness 倾向保留 `.gitignore`、`AGENTS.md`、README，以及可选的依赖清单。Grok 提议仅保留前两项。Codex 建议以零根文件为目标。README 可进 `.github/`，依赖清单可通过 `pip -r` 指定路径。`.gitignore` 和 `AGENTS.md` 是否应作为例外，取决于新克隆的忽略覆盖和实际 Agent 的规则发现，仍待裁决。[GitHub README 位置](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) · [Git 忽略规则](https://git-scm.com/docs/gitignore)
- **旧入口契约**：[REQ-002 v1.0](../requirements/SOURCE_LAYOUT_REQUIREMENTS.md) §2 要求原 Web 命令和 MCP 根脚本路径继续可用。采用下列目录树须接受调用方换路径并更新该条。若旧字面路径必须继续可用，两个根脚本也需要例外。

布局参考沿用 entry-3743 的版本盘点。Starlette 提供一级源码包的例子，Flask 提供明确入口的例子，Textual 提供测试和工具分区的例子。三个样本均保留部分根元数据。[Starlette 0.47.3](https://github.com/Kludex/starlette/tree/0.47.3) · [Flask 3.1.2 入口配置](https://github.com/pallets/flask/blob/3.1.2/pyproject.toml) · [Textual v5.3.0](https://github.com/Textualize/textual/tree/v5.3.0)

### 14.2 推荐目录树

以下为严格零根文件候选。树中列出用途目录及代表性文件。

```text
<仓库根>/
├── .git/                         # Git 元数据（普通克隆）
├── .github/README.md             # 仓库首页
├── config/
│   ├── requirements.txt
│   └── dsh-headless-auth.patch.yml
├── docs/
│   ├── README.md                 # 文档索引
│   ├── BACKLOG.md
│   ├── architecture/
│   ├── design/
│   ├── drafts/
│   ├── entry/AGENTS.md           # 严格方案下的本仓规则入口
│   ├── guides/MCP_SETUP.md
│   ├── history/
│   ├── requirements/
│   └── verification/
├── entrypoints/
│   ├── agent_bridge.py           # MCP
│   ├── inspect_execution.py      # 诊断
│   ├── main.py                   # 终端
│   └── web_chat.py               # Web
├── env/                          # 本地虚拟环境
├── postmortem/
├── runtime/                      # 默认数据库、会话和测试产物
├── specs/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   └── verify/
├── tools/
└── workbench/
    ├── adapters/
    ├── agents/
    ├── collaboration/
    ├── execution/
    ├── mcp/
    ├── prompts/
    ├── storage/
    ├── web/
    │   ├── rendering/page.py     # 内嵌页面
    │   ├── routing/routes.py      # HTTP 路由
    │   └── transport.py          # 响应写出
    └── workers/
```

Web 验收记录进入 `docs/verification/`。专题报告、旧协议稿及未被现行页面加载的 `collaboration_ui.js` 按实际用途归入草稿或历史，保留历史命令的版本语境。严格方案按目录配置忽略规则，在 `env/` 下重建虚拟环境。本仓规则位置变更只影响本仓，外部工作区继续读取自己的项目规则。

### 14.3 迁移、回滚与验收提案

1. 先归位渲染和路由，更新导入及夹具，验证 `GET /`、受影响 API 和前端测试。
2. 四个入口与调用方、现行说明同批切换。从仓库根运行 `python entrypoints/web_chat.py --port 8766` 或 `python entrypoints/main.py`。MCP 注册使用 `entrypoints/agent_bridge.py` 的绝对路径。诊断继续显式传入 `--db`，依赖安装使用 `python -m pip install -r config/requirements.txt`。从仓库外用绝对路径验证工作目录语义，并验证 MCP 握手、工具枚举及测试入口。
3. 单独迁移运行数据。停写并制作可验证备份，按 SQLite 一致性要求处理主库与仍有效的 WAL，由新位置按正常机制重建临时辅助状态。核对 Web 与 MCP 打开同一份有效库，读取既有会话，并保持 `AGENT_BRIDGE_DB` 覆盖能力。回滚前停写，将旧入口重新指向最新有效库或一致迁回，避免重新启用旧副本。终端会话一并检查。
4. 在新克隆及迁移工作区，于启动前、启动及测试后分别检查 `Get-ChildItem -Force -File -Name`。严格方案应无输出，折中方案应仅列获准例外。用 `git check-ignore -v` 核查凭据、缓存和运行产物的忽略覆盖，并检查根目录没有重新生成缓存或产物目录。

本节仅为提案。仓外 MCP 登记、Agent 规则发现、数据库占用及迁移和回滚仍待验证。

### 14.4 文档影响与随迁检查

本节补充 §9 和 §14.3。以下是仓内 Markdown 的静态盘点，表中列的是拟处理位置。批次沿用 §14.3 的四步。

| 文档与定位 | 影响与拟处理 | 批次 |
|---|---|---|
| [ARCHITECTURE.md](../architecture/ARCHITECTURE.md#L17) 控制流图及图后说明 | 第 1 批对照渲染与路由的新实现位置。第 2 批同步 Web 装配、MCP 入口及本仓规则发现的图节点和正文，按实际方案核对路径与箭头。 | 1、2 |
| [WORKBENCH_CALL_FLOW.md](../architecture/WORKBENCH_CALL_FLOW.md#L263) 源码定位表及各图下的依据 | §263 附近仍引用前次重组前的根源码与行号。重验现行调用链并补新定位，保留已评审图文对应的旧版本证据。同步 [架构索引](../architecture/README.md) 的摘要和验证范围。 | 1、2 |
| [流程治理教训](../lessons/PROCESS_GOVERNANCE_LESSONS.md)、[重启教训](../lessons/BENCHMARK_RESTART_LESSONS.md)、[教训索引](../lessons/README.md) | 正式教训以规范、讨论稿和事故复盘链接为主。按候选树这些目标仍在原位置。验收时复查分区导航、跨文档锚点以及现行规则入口。 | 2、4 |
| [P0-2 复盘](../../postmortem/P0-2_POSTMORTEM.md#L70)、[重启教训讨论](BENCHMARK_RESTART_LESSONS_DISCUSSION.md#L185) | 保留历史代码行号、验证命令与旧文件名，例如当时的 `web_chat.py` 行号和 `MCP_SETUP.md` 修改记录。复盘第 113 行的待办定位如仍供现行导航，补新入口并保留历史编号。 | 2、4 |
| 根 `README.md`、`MCP_SETUP.md`、`AGENTS.md`、`BACKLOG.md`、`WEB_COLLABORATION_ACCEPTANCE.md` | 同批更新现行安装、启动、测试和 MCP 配置。按规则入口裁决更新项目导航。逐一重算迁出文档的出链及仓内入链。第 3 批同步默认数据库、虚拟环境和诊断示例。旧验收命令保留原语境。 | 2、3 |
| `P0-2_OVERVIEW.md`、`P0-2_TASK6A_DESIGN.md`、`P0-2_PROBE_PLAN.md`、`P0-2_VERIFY_LOG.md`、`P0-2_DISCUSSION_RECORD.md`、`TEST_P02_SMOKE.md`、`TEST_TASK6_*.md`、`README.new.md`、`ISSUE*.md`、两份根协议稿 | 结合 §9 逐份判断现行操作或历史材料。保留旧实验和验收证据。仍可运行的诊断与验证指引补现行命令，迁出的文档核对相对链接和索引。 | 2、4 |
| 正式 `SOURCE_LAYOUT_REQUIREMENTS.md`、`SOURCE_LAYOUT_DESIGN.md` 与关联草稿，设计分区的并发验证、规范遵循、协作派发、故障可见性、Grok 适配、README 和 UI 简化设计 | 对 REQ-002 §2 按兼容裁决记录新旧契约。逐项核对命中的路径是现行入口、计划示例还是版本证据。历史草稿保留原定位并注明基线。 | 1–4 |
| [规范索引](../../specs/README.md)、[讨论规范](../../specs/AGENT_DISCUSSION_SPEC.md)、[设计留痕规范](../../specs/DESIGN_DECISION_RECORD_SPEC.md)、[文档索引](../README.md) | 核对待办与项目入口的现行定位，以及新增分区、文档迁入和往返导航。涉及规范正文的定位改动之后须依适用规范维护。 | 2、4 |

每批按已采纳的路径核对受影响文档的相对链接、章节锚点和代码定位，区分迁移前已失效的引用与该批引入的断链。对变动的架构图核对实际调用边并检查 Mermaid 渲染。按文档给出的工作目录执行现行命令，分别记录新克隆、存量迁移及外部工作目录的结果。仓外链接、书签、未识别的生成文档来源及实际迁移后的检查仍待验证。

## 15. 正式稿整理与上一轮检视记录

entry-3771 用户要求正式稿聚焦正式内容，草稿保留过程信息。接受该要求。拟将[正式设计](../design/SOURCE_LAYOUT_DESIGN.md) §15.1–§15.4 合并为「§15. 根目录与入口约定」，正文替换为：

> [REQ-002 v1.0](../requirements/SOURCE_LAYOUT_REQUIREMENTS.md) §2 继续适用，Web 与 MCP 沿用仓库根目录入口。根目录文件例外与旧入口路径的后续兼容要求待裁决。候选方案及其检视记录见[草稿 §14–§15](../drafts/SOURCE_LAYOUT_DESIGN.md)。

[设计索引](../design/README.md)对应摘要拟替换为「1.0 正式设计。实施与受控验收见 §14，根目录与入口的现行约定见 §15」。正式稿 §14 实施证据和原变更历史保留。候选目录、迁移与回滚提案、文档影响与随迁检查均可在本稿 §14.1–§14.4 查阅。

entry-3762 的两方独立只读检视通过，无阻断异议。送审 SHA-256 为正式设计 3CDE1073E7B8309D1812147C17B7640442D3594407C6CB68AA9283784BD23E5F、设计索引 656801AFD29057B233A5F5367F1984A4D958D69B13427C2CAD0C69843E715E19。两方均核对了版本与索引、共识与异议、候选布局、现状与计划、迁移验收及文档影响。两方建议明确测试产物去向和入口脚本的模块搜索路径。DeepSeek Harness 认为树中列出 `.git/` 易引起误读，Grok 认为标注为 Git 元数据即可。Grok 另建议摘要提供续议导航。这些意见对应原送审版本。迁移、回滚、仓外调用、规则发现、数据库占用及 Mermaid 渲染均未实测。

## 16. 根文件确认与入口迁移可行性

### 16.1 已确认范围

用户在 entry-3797 同意常设保留四个根文件。此裁决更新 §14 的零根文件候选前提。entry-3803 要求整理文档供审阅，入口切换及旧路径兼容期限仍待裁决。

| 文件 | 保留理由 |
|---|---|
| `.gitignore` | 从仓库根维护全仓忽略规则，覆盖本地环境与运行产物。 |
| `AGENTS.md` | 保留项目规则入口，方便 Agent 从仓库根发现规范索引。 |
| `README.md` | 保留仓库首页说明和文档导航。 |
| `requirements.txt` | 保留现行依赖清单和安装命令的入口。 |

### 16.2 方案取舍与证据

entry-3786 比较了 Flask 的稳定命令、Starlette 由外部 ASGI 服务加载应用，以及 Textual 将同名 CLI 迁入独立发行包。这些案例说明公开启动命令可以和源码位置解耦。项目能否退役旧路径，取决于本项目调用方能否同步切换。

- **直接切换**。候选将 Web、MCP、终端和诊断入口归入 `entrypoints/`，调用方同步改用新路径。优点是每个入口只有一套公开启动位置。前提是已识别的调用方能同步改写并通过验收。
- **限期兼容**。如果部分调用方不能同步更新，可提议为受影响的旧入口保留薄转发脚本，新旧入口共用同一实现。代价是过渡期多一套公开路径与验证工作，须另定退役条件及期限。四项常设根文件的裁决已确定，兼容脚本属于待审阅的临时安排。

entry-3797 已确认两处本机磁盘配置用项目内 Python 启动仓库根的 `agent_bridge.py`，并显式指向现有数据库。绝对脚本路径使 stdio MCP 启动不依赖客户端的工作目录。

| 调用方 | 已观察 | 待验证 |
|---|---|---|
| Codex | 用户级 MCP 登记的脚本参数使用旧绝对路径，数据库由 `AGENT_BRIDGE_DB` 显式指定。当前仓库未发现项目级 Codex 配置。 | 生效会话、重载后的握手和工具发现。 |
| DeepSeek Harness 桌面 | 用户级 desktop profile 的 Cordis 补丁登记了旧绝对路径和现有库。 | 生效会话、重载后的握手和工具发现。 |
| DeepSeek Harness 网页工作台 | 工作台直接启动 DSH CLI，传入 headless profile 和补丁。已查可见 headless、工作台命名 profile 及仓库默认补丁未见桥接登记。 | 运行中进程是否通过 `DSH_HEADLESS_PATCH` 指定了其他补丁，及新 Web 入口下的实际调用。 |
| Grok Build | 已查用户级配置、当前仓库和工作台显式启动参数，未发现桥接登记。工作台直接启动 Grok CLI 并读取流式 JSON。 | 其他工作目录、管理覆盖或独立客户端的有效配置，以及新 Web 入口下的实际调用。 |

已知旧登记可随脚本位置改写，现有证据未发现必须永久保留旧入口的技术障碍。当前只查过磁盘配置和启动代码，运行态加载、握手及回滚均未实测。

### 16.3 候选迁移、验收与回滚

1. 入口策略获批后，先更新 REQ-002 §2 的入口约定，盘点仓内外启动命令、实际客户端配置和覆盖。记录原启动参数、补丁及真实数据库路径，为回滚准备原入口及配置备份。
2. 在入口批次归位脚本。Web 和 MCP 当前据脚本文件位置推导项目根及默认库，归位后同步校正根定位、模块搜索路径及相对于项目根的资源与补丁解析。先沿用原数据库，对照 Web 默认库、无覆盖的 MCP 默认库和两端客户端显式库路径，防止在新目录误建空库或分库。运行数据归位作为独立批次。
3. 更新两处已确认 MCP 登记的脚本参数、Web 启动命令与受影响引用。核对运行中 DSH 的实际补丁和 Grok 有效覆盖，发现旧路径时纳入切换。按 §14.4 同步现行指引，保留历史命令的版本语境。
4. 分别从仓库根及仓外工作目录启动。仓外用绝对入口路径，核对 Web `--port`、终端和诊断入口及工作台实际发起 DSH 与 Grok 请求。重载 Codex 与 DSH 桌面后，各自完成 stdio MCP 初始化、工具列表查询和一次只读查询。核对 Web 与 MCP 的解析后库路径和同一条存量记录。若选择限期兼容，同样验证旧命令能转发到同一实现和同一库。
5. 新入口无法启动、握手失败或出现分库时，停止切换，将旧脚本、启动命令及客户端登记一并恢复，重载后复验旧入口与共库。如果后续独立批次迁库，先停写并备份，核对切换后新增写入再确定恢复源，恢复路径配置后校验数据，避免直接覆盖有新增写入的库。

### 16.4 正式文档拟同步内容

[正式设计 §15](../design/SOURCE_LAYOUT_DESIGN.md) 拟保留标题「根目录与入口约定」，正文更新为：

> 仓库根目录常设保留 `.gitignore`（全仓忽略规则）、`AGENTS.md`（项目规范入口）、`README.md`（项目首页和导航）及 `requirements.txt`（依赖清单和安装入口）。
>
> [REQ-002 v1.0](../requirements/SOURCE_LAYOUT_REQUIREMENTS.md) §2 继续适用，Web 与 MCP 沿用仓库根目录入口。入口迁移和旧路径兼容策略待审阅，候选与查证依据见[设计草稿 §16](../drafts/SOURCE_LAYOUT_DESIGN.md#16-根文件确认与入口迁移可行性)。

[设计索引](../design/README.md)对应摘要拟更新为「1.0 正式设计。实施与受控验收见 §14，已确认的常设根文件与现行入口见 §15」。REQ-002 §2 的有效启动契约继续适用。


## 17. 根入口保留与 Markdown 归位决定

用户在 entry-3797 确认根目录常设 `.gitignore`、`AGENTS.md`、`README.md` 和 `requirements.txt`，理由见 §16.1。entry-3808 查证表明，从源码直接运行的应用和安装后提供命令的工具都有公开案例。根脚本是本项目既有启动接口，Web 命令含 `--port`，已有 Codex 和 DeepSeek Harness 桌面配置登记根 MCP 脚本路径。用户在 entry-3813 确认保留根 Web/MCP 入口，继续遵循 REQ-002 §2。本次采用该决定，§14–§16 的布局与入口迁移候选保留为讨论历史。

entry-3813 的只读盘点和两方评议形成下列清单，用户在 entry-3821 批准实施。行为规范由 `specs/` 管理，说明、计划、设计、验证与历史证据按用途归入 `docs/`。本节记录已决定的目标，实施和检查结果由后续任务补记。

| 现有根文件 | 批准的目标 |
|---|---|
| `README.md` | `README.md` |
| `AGENTS.md` | `AGENTS.md` |
| `BACKLOG.md` | `docs/BACKLOG.md` |
| `MCP_SETUP.md` | `docs/guides/MCP_SETUP.md` |
| `README.new.md` | `docs/drafts/README.new.md` |
| `AGENT_COLLABORATION_SPEC.md` | `docs/archive/collaboration/AGENT_COLLABORATION_SPEC.md` |
| `COLLABORATION_PROTOCOL.md` | `docs/archive/collaboration/COLLABORATION_PROTOCOL.md` |
| `WEB_COLLABORATION_ACCEPTANCE.md` | `docs/verification/WEB_COLLABORATION_ACCEPTANCE.md` 与 `docs/guides/WEB_COLLABORATION_GUIDE.md` |
| `ISSUE8_FIX_PLAN.md` | `docs/archive/issue-8/ISSUE8_FIX_PLAN.md` |
| `ISSUE8_REMAINING_TESTS.md` | `docs/archive/issue-8/ISSUE8_REMAINING_TESTS.md` |
| `ISSUE8_VERIFY_TEST.md` | `docs/archive/issue-8/ISSUE8_VERIFY_TEST.md` |
| `ISSUE_CHECK_CONSTRAINT_REGRESSION.md` | `docs/archive/issue-8/ISSUE_CHECK_CONSTRAINT_REGRESSION.md` |
| `P0-2_DISCUSSION_RECORD.md` | `docs/archive/p0-2/P0-2_DISCUSSION_RECORD.md` |
| `P0-2_OVERVIEW.md` | `docs/archive/p0-2/P0-2_OVERVIEW.md` |
| `P0-2_PROBE_PLAN.md` | `docs/archive/p0-2/P0-2_PROBE_PLAN.md` |
| `P0-2_TASK6A_DESIGN.md` | `docs/archive/p0-2/P0-2_TASK6A_DESIGN.md` |
| `P0-2_VERIFY_LOG.md` | `docs/archive/p0-2/P0-2_VERIFY_LOG.md` |
| `TEST_P02_SMOKE.md` | `docs/archive/p0-2/TEST_P02_SMOKE.md` |
| `TEST_TASK6_LIFECYCLE_BREAKAWAY.md` | `docs/archive/p0-2/TEST_TASK6_LIFECYCLE_BREAKAWAY.md` |
| `TEST_TASK6_MECHANISM.md` | `docs/archive/p0-2/TEST_TASK6_MECHANISM.md` |
| `TEST_TASK6_PREREQS.md` | `docs/archive/p0-2/TEST_TASK6_PREREQS.md` |

**评议取舍。** 两方确认 BACKLOG 迁移要同步现行路径。采用 Grok 关于路径与普通提及的区分，保留设计留痕规范中对 BACKLOG 的阶段约束。两份协议稿作为历史归档，运行时 JSON 以解析器为准，Agent 讨论行为以讨论规范为准。保留旧协议中仍与实现相符的 Issue #8 条款及已过时 Stage 1 状态的版本语境。两方均支持将 Web 日常操作和 2026-10-20 验收拆开，后半的早期计划与待验收项仍须保留各自的状态。按 Grok 补查修正初评，P0-2 冒烟有历史执行证据，三份 Task 6 说明保留原有未实测状态，Issue #8 取消后的任务卡及进程退出仍有缺口。脚本现存位置与实测结论分别记载。

**正式设计拟改。** §15 保留四项常设根文件及理由，将入口段改为「按用户在 entry-3813 的决定，Web 与 MCP 保留仓库根目录入口，REQ-002 §2 继续适用。」增加「项目待办、操作指南、草稿、验证记录及专题历史按用途归入 docs/，逐件映射与讨论依据见草稿 §17。」在该节补记实施与验证范围，避免把旧候选或预期检查写成结果。

**正式文档与导航拟改。** 根 README、AGENTS、规范索引和讨论规范中的有效 BACKLOG 指向新清单，保留原事项编号与锚点。根 README 的 MCP 接入链接、REQ-002 的指南引用，以及设计、草稿、复盘和其他文档的有效相对入链出链按上表重算。历史版本的命令、路径和状态保留当时语境。正式设计和草稿索引指向 §15、§17，docs 总索引新增待办、指南、归档入口，指南及归档索引列出文件与用途，验证索引登记 Web 历史验收。README 候选稿在草稿索引标为提案。两份协议稿增加历史说明及现行解析器链接。

**联调拆分拟改。** 将超时配置、当前回归命令、启动步骤和经核对仍适用的操作说明写入指南。原验收记录保留 2026-10-20 结果、当时命令、未完成的真实联调场景和后续历史材料，并链接现行指南。指南反向链接验收记录。历史计划与问题声明应标明时点，尤其是未闭环的 WinError 206 记录。

**实施前核对。** 21 个来源存在，目标检查未发现冲突。BACKLOG、MCP 接入指南、根 README、Web 联调记录以及若干设计和索引已有工作树改动，迁移以现有内容为准。盘点已覆盖可见仓内引用，仓外书签、个人脚本和生成引用尚未证实。实施后核对前后清单、索引、相对链接和锚点，并由两位 Agent 检视同一版本。

**实施记录。** 按上表完成文档归位和 Web 联调拆分，更新现行入口、文档索引及相对引用。根 Web 和 MCP 启动脚本沿用现行调用方式。旧验证脚本和真实联调未重跑，独立检视待完成。

## 变更历史

| 时间 | 版本／议题 | 原状态或决定→新状态或决定 | 原因与证据 | 提出／决定者及来源 | 影响范围 |
|---|---|---|---|---|---|
| 2026-11-20 11:21:44 +08:00 | entry-3821／文档归位实施 | 已决定→已实施，待独立检视 | 用户批准，目录重组草稿 §17。本机时钟 | Codex 实施 | §17、布局与引用 |
| 2026-11-20 11:20:01 +08:00 | entry-3821／根文档归位决定 | 入口候选与文档分类建议→保留 Web/MCP 根入口并批准文档归位 | entry-3797、3808、3813 及本轮用户批准。本机时钟 | 用户决定，Codex 整理 | §17，实施及验证待后续记录 |
| 2026-11-20 10:51:21 +08:00 | entry-3803／入口迁移可行性 | 根文件待裁决→四项已确认，入口策略待审阅 | 用户确认、entry-3786 与 entry-3797，见 §16。本机时钟 | 用户确认，Codex 整理 | 草稿依据、候选验收与正式文档拟稿 |
| 2026-11-20 09:35:36 +08:00 | entry-3771／正式稿整理留稿 | §14 续议→补充整理拟稿与送审结论 | 用户要求与 entry-3762 同版检视记录。本机时钟 | 用户授权，Codex 整理 | 保留候选与检视语境 |
| 2026-11-20 09:08:48 +08:00 | entry-3758／文档影响 | 通用随迁约定→补充受影响文档与批次 | 仓内引用盘点及 §14.4。本机时钟 | 用户提出，Codex 整理 | 架构、教训、复盘与其他文档的拟处理及检查 |
| 2026-11-20 08:48:28 +08:00 | entry-3754／根目录归位续议 | v0.2 评审记录→保留历史并增补待审提案 | entry-3743 盘点与两方意见，见 §14。本机时钟 | 用户委托，Codex 整理 | 增补目录树、兼容取舍及验收计划 |
| 2026-11-20 00:52:04 +08:00 | 0.2／entry-3721 | 草案→文档影响与验收待审稿 | entry-3717 盘点，见 §9–§12。本机时钟 | 用户委托，Codex 修订 | 文档、入口、分批验收和历史引用 |
| 2026-11-20 00:25:49 +08:00 | 0.1／entry-3706 | 无→设计草案 | REQ-002 与两份独立分析，见 §13。时间源为本机时钟 | 用户委托，Codex 综合 | 目录、拆分、兼容与分批验收，待审阅 |
