# Marketing Brain 设计文档（V0.1 + V0.2）

> 面向研发的设计要点：系统采用何种架构、模块如何分工、关键契约与决策。
> 不重复代码中已有的函数签名、字段清单等实现细节，只记录意图、决策与边界。
> 稳定的系统级架构见 `docs/architecture.md`，本稿聚焦已落地部分的功能点与业务契约。
> V0.2 的模块设计要点与关键决策见 §7。

## 1. 产品定位与运行框架

Marketing Brain 不是自由运行的多 Agent 聊天系统，而是**确定性工作流骨架 + 受控 Agent 探索 + 全过程审计评估**的营销策略生产系统。

V0.1 落在舆情分析场景，核心运行框架是：

```text
用户自然语言问题
→ 任务理解（意图识别）        —— 自由文本 → 结构化 AnalysisIntent
→ 逻辑快照（数据边界）        —— 记录口径，不拷贝数据
→ 数据程序基础统计            —— 确定性查询，不依赖模型
→ （后续阶段）主Agent/子Agent    —— V0.2+ 受控探索
→ 报告与证据
```

V0.1 的关键取舍：**先把「确定性骨架 + 数据链路 + 意图解析」跑通并验证数据真实可用**，Agent 能力留到 V0.2+。这一步不追求 Agent 智能，而是先建立一条真实、可核验、边界清晰的链路。

## 2. 模块设计与分层

后端为**模块化单体**（Python FastAPI），按职责清晰分模块，为后续替换、插拔留口。

```text
┌─────────────────────────────────────────────┐
│ api/     REST 接口与请求/响应契约             │
├─────────────────────────────────────────────┤
│ understanding/  任务理解 → 结构化意图          │
│ snapshot/       逻辑快照 → 数据边界             │
│ datasource/     数据源适配器 → 只读查询          │
│ store/          状态持久化 → SQLite 任务与事件   │
│ core/           配置与环境加载                 │
└─────────────────────────────────────────────┘
```

### 2.1 任务理解（understanding/）

**决策**：输入始终是自由文本，不做老式「下拉选车型」的 IT 模式。未来所有问题输入都可能来自自由文本。

V0.1 采用**规则 +关键词匹配**解析出 `AnalysisIntent`（分析对象 object、时间周期 time_range、目标类型 goal_type）。`IntentParser` 对外接口固定，未来升级为 LLM 意图识别时**接口不变**，只换实现。

- 内置关键词表（`BUILTIN_KEYWORDS`）覆盖常见汽车品牌/车型/话题，按**长度降序**优先匹配长复合词（如「猛士M817」优先于「猛士」），避免误拆。
- 目标类型三档：`pulse`（常规脉搏）/ `anomaly`（异常）/ `drill`（专题下钻），由触发词判别。
- `TimeRange.resolve_relative` 把「近期 / 上周 / 上个月 / 最近N天 / YYYY年M月」等相对短语解析为绝对日期区间。

这个模块是 V0 从「IT 系统」走向「营销通用 Agent」的第一步。

### 2.2 逻辑快照（snapshot/）

**决策（重要）**：采用**逻辑快照**，不复制几十万条原始数据。

创建任务时记录**数据边界**（数据源、时间窗、最大 ID），之后本次运行的所有工具调用自动附加同一边界，保证**一次运行内部口径一致**。快照可序列化存入 SQLite，避免为 V0 建设数仓或对象存储。详见开发计划 3.6 节。

### 2.3 数据源适配器（datasource/）

**核心原则：模型/Agent 不直接连接数据库，不执行任意 SQL。**

`MySqlDataSource` 只暴露**能力有限的预定义方法**（`fetch_comments` / `data_overview` / `distinct_video_titles`），调用方无法传原始 SQL。每个连接额外强制 `SET SESSION TRANSACTION READ ONLY`，与「账号仅 SELECT」形成双保险。

真实数据来自**测试环境 MySQL 的 `api_job` 表**（约 97 万条评论），而非临时测试的 `comment` 表。评论以 JSON 存储在 `request_payload` 列，通过 MySQL `JSON_TABLE` 展开；`like` 是 MySQL 保留字，需反引号转义。数据读取细节见 `docs/评论数据获取说明.md`。

### 2.4 状态持久化（store/）

用 SQLite + 本地持久化目录保存任务、运行事件（审计）、结果。通过 Repository 接口为后续更换数据库（MySQL/PostgreSQL）留口。

- `TaskRepository`：任务主档（输入、解析意图、快照、状态、结果）。
- `EventRepository`：追加式运行事件（审计时序），带自增 `seq` 保证顺序。

### 2.5 配置（core/）

从 `.env` / 环境变量加载。**业务配置（数据库连接）必须显式提供**，缺项抛出清晰的 `ValueError`；应用运行参数提供默认值。密码特殊字符做 URL-encode。

## 3. 关键设计决策（已定）

| 决策点 | 结论 | 理由 |
|---|---|---|
| 输入方式 | 自由文本 + 任务理解模块，不做下拉选车型 | 走向营销通用 Agent，未来输入多来自自由文本 |
| 部署形态 | 本地开发验证、测试环境 Docker Compose 部署 | 本地无 Docker；预编写脚本但不在本地打包 |
| 数据快照 | 逻辑快照（记录边界），不复制数据 | 几十万条数据规模大，避免建数仓/对象存储 |
| 数据源 | `api_job` 正式数据（非 `comment` 测试表） | 接入真实数据，约 97 万条评论 |
| DB 权限 | 独立只读账号（仅 SELECT） | 数据源只读，绝不修改原始数据 |
| 模型能力 | V0.1 先建骨架与链路，Agent/LLM 留待 V0.2+ | 小步验证，先确认数据真实可用 |

## 4. 插件化预留（V0.1 现状 vs V1 方向）

| 接口 | V0.1 实现 | V1 扩展方向 |
|---|---|---|
| DataSource | MySQL `api_job` 只读适配器 | 其他数据库、文件、外部接口 |
| AnalysisTool | （V0.1 由数据源预定义方法承担） | 8 个业务统计/证据工具 |
| Skill | （V0.2+ 引入） | 文件化舆情 SOP、输出协议 |
| ModelProvider | 预留配置项 `LLM_*` | OpenAI-compatible 真实 LLM API |
| EventStore | SQLite 任务/事件仓库 | MySQL/PostgreSQL、外部监控 |

插件接口只解决**能力替换**问题；预算控制、事件记录、证据绑定、质量门禁属于运行内核，不允许普通 Skill 绕过（开发计划 3.4 节）。

## 5. 数据安全与审计约束

- **只读**：数据源账号仅 SELECT，适配器每个连接还强制只读会话——双保险。
- **默认分页/限额**：`fetch_comments` 等接口带 `limit` 防止单次拉取过大；`TOOL_MAX_RECORDS` 约束工具返回条数。
- **审计事件**：任务创建、快照建立、计划生成等结构化事件追加进 `EventRepository`。审计对象是「计划、动作、证据、结构化判断与状态变化」，**不是模型不可验证的原始思维链**（开发计划 3.7 节）。

## 6. 当前边界（V0.1 不做）

- 真实 LLM 调用与 Agent 规划/下钻（V0.2+）；
- 全量向量化与复杂主题聚类；
- 实时流式舆情监测、全网数据采集；
- 多 Agent 自由协作、自动舆情响应、长期记忆与经验进化；
- 生产级高可用、微服务、分布式调度；
- 用户权限、多租户、复杂图表平台。

## 7. V0.2 模块设计要点与关键决策

V0.2 在既有分层上新增三个子模块：`llm`、`analysis`、`pipeline`，不改既有模块对外接口。本版目标是**建立可对照的非 Agent 基线**。

### 7.1 新增模块职责

| 模块 | 职责 |
|---|---|
| `llm/provider.py` | OpenAI-compatible Provider。`httpx` 直连、不引入 openai SDK；核心是 `chat` / `chat_json`（后者请求 `response_format=json_object` 并校验）。每次调用返回结构化摘要（模型名、输入/输出 token、耗时）。抽象为接口，测试可用录制响应 stub 替换。 |
| `llm/report.py` | 报告生成。把流水线结构化中间结果（统计 + 抽样评论 + 证据 ID）整理成上下文，调用一次 LLM 按固定输出协议生成「舆情策略包」JSON。 |
| `analysis/tools.py` | 确定性统计工具集。V0.2 首批约 8 个（数据覆盖/质量、声量趋势、当前 vs 对比、主题词频、头部来源、分层抽样、下钻、对象比较）。所有工具自动附加快照边界，返回查询条件、统计结果、样本量与偏差提示。 |
| `pipeline/baseline.py` | 固定流水线。阶段顺序硬编码、不做任何 LLM 自主规划，逐步产出结构化中间结果累积为 `AnalysisBundle`。 |
| `pipeline/verify.py` | 报告引用校验。从报告提取引用的 `comment_id` / `job_id`，逐一对照本次运行的证据库；合法保留、非法剔除并记录到 `validation.rejected_refs`。 |
| `pipeline/runner.py` | 任务执行器。用标准库 `threading` 后台执行，编排「取证 → 报告 → 校验 → 落库」，全程写追加式事件；失败保留错误、不生成伪报告。 |

### 7.2 关键决策

- **固定（非 Agent）流水线**：V0.2 明确不引入 Agent 规划/多步下钻，只为「数据取证 → 报告」建立一条写死的基线链路，供后续 V0.4 的 Agent 对照验证。两阶段 Agent 式分析是 V0.4，不在本版。
- **报告是固定结构的「舆情策略包」**：一次 LLM 调用按固定输出协议产出，字段组含 `scope / overall / themes / sources / risk_opportunity / evidence_gaps / assumptions / actions / metrics`，作为后续版本契约打底。
- **引用校验对着证据索引**：报告引用的证据 ID 必须来自喂给模型的证据库；未知 ID 直接剔除并在报告中标注，杜绝幻觉证据混入。
- **推理模型输出预算**：deepseek 系推理模型会把输出预算大量用于 `reasoning_content`，若人为设 `max_tokens` 偏低会在推理阶段耗尽、最终答案为空（`finish_reason=length`）。故报告请求**不设** `max_tokens`，输出上限交由模型自身决定；超时用不低于 300s（覆盖默认的 120s）。
- **后台线程 + 前端轮询**：`POST /api/tasks` 立即返回 `task_id`，后台线程驱动流水线，前端按 `UI_POLL_INTERVAL_MS` 轮询状态；运行中任务展示模型/Token/耗时。

## 8. V0.4 模块设计要点与关键决策

V0.4 是「主 Agent 与受控子 Agent Loop」里程碑：把 V0.2 的固定基线、V0.3 的分阶段工作流再往前推一步，让 Agent 在**确定性骨架内**自主探索，同时保持全过程可审计、可评估。本版不引入 V0.2/V0.3 之外的既定版本，`app/pipeline/` 与 `app/workflow/` 均保持不变。

### 8.1 关键决策

- **真 Function Calling（方案 A）**：子 Agent 用 LLM 的原生 `tools` / `tool_choice=auto` 机制声明并调用工具，模型侧通过 `LLMResult.tool_calls` 返回结构化调用（含 JSON 参数），而非让模型输出文本再解析。参数仍需 `validate_and_coerce` 按 schema 钳制，工具名必须在白名单内。相比「文本驱动」方案，这消除了模型臆造工具名/参数格式的歧义，也便于审计。
- **预算硬限制**：`AgentBudget` + `BudgetCounter` 四维上限（子任务 / 单卡循环 / 工具调用 / 补查），任一超限即停止。预算、停止条件、评审门禁属于运行内核，普通子 Agent 无法绕过。
- **评审门禁 + 一次受控补查**：`Reviewer` 独立评审，`request_supplement` 触发一次受控补查（`AGENT_MAX_SUPPLEMENTS=1`），再评审后定稿。评审是质量门，不是表面步骤。
- **三层报告**：调查结果分事实层（证据）、解释性判断（theme/risk/opportunity）、待验证假设（assumption）。Investigator 工具只登记事实层，`Orchestrator` 在综合阶段把子任务的 `findings`/`hypothesis` 补登为 judgment/assumption，保证解释层不恒空。
- **V0.3 保留**：V0.4 不替换 V0.3。`ENABLE_AGENT_ENGINE` 与 `ENABLE_WORKFLOW_ENGINE` 两个开关决定路径（Agent → Workflow → V0.2），三者可各自回退，历史任务不受影响。

### 8.2 角色与边界

- **Supervisor**：只规划（出调查卡），不调用数据工具。
- **Investigator**：受控 Loop，用 Function Calling 下钻，受预算与白名单夹逼；输出非法时重试一次后以 `illegal_output` 停止。
- **Reviewer**：只评审，不执行工具；`pass` / `request_supplement`。

### 8.3 测试与验证

- 单元测试覆盖协议、预算、工具 schema、三角色与编排（`test_protocols` / `test_budgets` / `test_tools_spec` / `test_supervisor` / `test_investigator` / `test_reviewer` / `test_orchestrator`）。
- 集成测试验证一条真实下钻链（`sample_comments → drill_evidence`）与三层报告、事件序列（`test_agent_integration`）。
- 真实 LLM + MySQL 冒烟（`test_v04_smoke`，`-m manual` 执行）端到端验证三角色 Loop。
