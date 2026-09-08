# Marketing Brain V0.1 + V0.2 + V0.3 架构

> 系统级信息：系统边界、核心模块职责、分层、数据流、外部依赖、关键技术约束。
> 变更系统形态时更新本文件。

## 1. 系统边界

Marketing Brain V0 是一套面向汽车舆情分析的轻量受控 Agent Harness。V0.1 + V0.2 + V0.3 边界：

**V0.1 负责：**
- 通过 Docker Compose 在测试环境启动；
- 正确读取测试环境 MySQL 的真实舆情数据（`api_job` 表，约 97 万条评论）；
- 把用户的自由文本输入解析为结构化分析意图；
- 建立逻辑数据快照（记录边界，不复制数据）；
- 持久化任务、运行事件、结果（SQLite）。

**V0.2 负责（非 Agent 基线）：**
- 接入真实 LLM API；
- 确定性统计工具集取证；
- 固定流水线生成舆情策略包报告（一次 LLM 调用）；
- 报告引用校验（未命中证据库的非法 ID 剔除）；
- 后台线程执行 + 前端轮询查看状态/报告。

**V0.3 负责（受控工作流 / Skill 机制）：**
- Skill 机制：YAML 声明式定义分阶段工作流（stages + 工具白名单 + prompts + output_schema）；
- 工作流引擎：按 stage 顺序执行、运行时工具授权拦截；
- 细粒度审计事件（skill_selected / stage_start / stage_done / tool_call / tool_unauthorized / judgment_made）；
- 证据引用图：judgment / assumption 证据类型，记录双向引用；
- 默认启用，可通过 `ENABLE_WORKFLOW_ENGINE` 回退到 V0.2 基线；

**V0.3 不负责（后续版本）：**
- 主子 Agent Loop（V0.4，两阶段 Agent 式流程届时才引入）；
- 完整审计可视化工作台（V0.5）。

## 2. 核心模块职责

| 模块 | 路径 | 职责 |
|---|---|---|
| 配置 | `app/core/config.py` | 从 `.env` 加载运行配置。数据库配置必须显式提供，缺失时清晰报错。 |
| 数据源 | `app/datasource/` | MySQL 只读适配器。`adapter.py` 暴露预定义查询方法，禁止任意 SQL。 |
| 任务理解 | `app/understanding/` | 从自由文本解析结构化意图（分析对象、时间范围、目标类型）。V0.1 用规则+关键词，V0.2 接 LLM。 |
| 逻辑快照 | `app/snapshot/` | 记录数据边界（时间窗、最大ID），保证一次运行口径一致。 |
| 状态存储 | `app/store/` | SQLite 持久化任务、事件、结果。通过 Repository 接口为 V1 换库留口。 |
| API | `app/api/` | REST 端点：健康检查、任务创建/查询、数据概览。`create_app` 工厂便于测试注入。 |
| LLM | `app/llm/` | OpenAI-compatible Provider（直连不做 SDK 封装）+ 舆情策略包报告生成。抽象为接口便于测试 stub。 |
| 确定性分析工具 | `app/analysis/` | 工具集合，自动附加快照边界（时间窗 + 对象标签），返回查询条件、统计结果、样本量、证据 ID 与偏差提示。 |
| 固定流水线 | `app/pipeline/` | 固定阶段顺序执行（数据取证 → LLM 报告 → 引用校验 → 落库），后台线程执行 + 前端轮询。 |

### V0.3 新增模块

**app/skill/**

- `schema.py`：SkillDefinition / StageDefinition 数据结构
- `loader.py`：从 YAML 加载 Skill，版本校验（只接受 0.3.0），缓存

**app/workflow/**

- `engine.py`：WorkflowEngine，按 Skill stages 顺序执行，工具授权在运行时拦截
- `context.py`：构建 Stage 专用 LLM 上下文（system prompt + 已有证据摘要）
- `runner.py`：后台线程启动工作流任务（替代 V0.2 的 BaselinePipeline runner）

**app/analysis/**

- `registry.py`：工具注册表（工具名 → 可调用对象映射）

**skills/**

- `opinion-pulse.yaml`：常规舆情脉搏分析
- `evidence-review.yaml`：证据复核专项
- `strategy-synthesis.yaml`：策略综合专项

### V0.4 新增模块

**app/agent/**（主 Agent 与受控子 Agent Loop）

- `protocols.py`：Agent 协议数据结构。`InvestigationCard`（主 Agent 拆解出的调查卡）、`InvestigatorResult`（子 Agent 单卡执行结果）、`ReviewVerdict`（评审结论）与五个停止原因常量（`evidence_sufficient` / `data_insufficient` / `budget_exhausted` / `tool_failure` / `illegal_output`）。
- `tool_spec.py`：工具 schema 与白名单。`TOOL_JSON_SCHEMAS`（供 Function Calling 注入）、`TOOL_WHITELIST`、`validate_and_coerce`（参数按 schema 钳制）、`ToolSpecError`。
- `budgets.py`：预算硬限制。`AgentBudget`（子任务/轮数/工具调用/补查上限）、`BudgetCounter`（累计与超限判定）、`BudgetExhaustedError`。
- `supervisor.py`：主 Agent（规划）。从意图与既有证据拆解 `InvestigationCard[]`，`parse_cards` 过滤越界建议工具。
- `investigator.py`：子 Agent（受控 Loop）。用真实 Function Calling 从白名单选工具、依据结果决定下钻或停止。
- `reviewer.py`：独立评审。输出 `pass` / `request_supplement`（至多一次受控补查）。
- `orchestrator.py`：编排。确定性骨架串联 Supervisor → Investigator[] → Reviewer → 三层报告，登记 judgment/assumption。
- `runner.py`：后台/同步运行器。`run_agent_sync` / `start_agent_background`，与 V0.2/V0.3 runner 结构一致。

V0.2 基线（`app/pipeline/`）与 V0.3 工作流（`app/workflow/`）**保持不变**。`app/api/` 的 `create_app` 新增 `require_sync`（测试专用，同步驱动 Agent/Workflow/V0.2，生产行为不变）。

## 3. 分层与数据流

```
前端 (Vite+React)
   │  /api/* (REST + 2-3s 轮询)
   ▼
FastAPI (app/api)
   │
   ├── 任务理解 (understanding) ──► 结构化意图
   ├── 逻辑快照 (snapshot)      ──► 数据边界
   ├── 确定性分析工具 (analysis) ──► 附加快照边界的统计取证
   ├── 固定流水线 (pipeline)     ──► 后台线程：取证 → LLM 报告 → 引用校验 → 落库
   ├── LLM (llm)               ──► OpenAI-compatible Provider + 策略包报告
   ├── SQLite 存储 (store)      ──► 任务/事件/结果/证据
   └── 数据源 (datasource)      ──► 测试环境 MySQL（只读）
```

## 4. 外部依赖

| 依赖 | 用途 | 关键约束 |
|---|---|---|
| 测试环境 MySQL (`drive_intent_backend.api_job`) | 舆情分析原始数据 | **只读账号**（仅 SELECT）。数据约 97 万条评论。 |
| LLM API | V0.2 已启用 | **OpenAI-compatible** 接口，`httpx` 直连（不引入 openai SDK）。`.env` 配置 base_url / key / model。推理模型输出预算大，报告请求**不设** `max_tokens`（上限交模型自身决定），超时 ≥300s。 |

## 5. 关键技术约束

1. **只读**：数据源账号仅授 SELECT，连接后强制只读事务；调用方无法执行任意 SQL。
2. **逻辑快照**：不复制几十万条原始数据，只记录边界，保证一次运行口径一致。
3. **任务理解优先**：输入始终为自由文本，通过意图识别模块结构化；不设"前端选择车型"模式。
4. **编码**：数据库 `utf8mb4`；Windows 终端默认非 UTF-8，脚本需 `sys.stdout.reconfigure(encoding='utf-8')`。
5. **`like` 保留字**：`api_job` 取数 SQL 中 `comment_like_count` 列需反引号转义。
6. **数据字段**：`preset_brand` / `preset_model` 为 NULL 不可用；品牌/车型/话题需从 `video_title` 提取。
7. **V0.2 数据口径（已实测确认）**：时间锚点统一用 `api_job.created_at`（UTC），不用评论自带时间（后者不可靠）；对象匹配用 `video_title` 中的 `#话题标签`，不做标题子串 `LIKE`（会把竞品对比视频误算进声量）；「来源（视频）」身份 = `video_title`（同一视频横跨数十个作业，单个作业为 **≤50 条评论的批次**），头部来源按 `video_title` 聚合并以代表性 `job_id`（该标题下最小 `api_job.id`）作为可回查的溯源 ID。

## 6. 部署

- **Docker 多阶段构建**：前端 Vite 编译产物由 FastAPI 统一提供。
- **单应用服务 + 持久化卷**：`docker-compose.yml` 只启动一个 app 服务，挂载 `mb-app-data` 卷。
- **直接连接测试环境 MySQL**：不在 Compose 中复制 MySQL。
- **本地开发**：Vite dev server (5173) 代理 `/api` 到后端 (19783)；后端用 `python -m app.main`。
