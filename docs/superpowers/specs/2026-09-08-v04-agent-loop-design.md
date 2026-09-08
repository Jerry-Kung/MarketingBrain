# Marketing Brain V0.4 设计文档：主 Agent 与受控子 Agent Loop

**日期**：2026-09-08
**版本**：V0.4
**状态**：设计已完成，待实现

---

## 1. 概述

### 1.1 目标

V0.4 实现 V0 最核心的 Agent 验证能力：**主 Agent 自主规划、子 Agent 在边界内有限探索、独立评审把关**。核心目标：

1. **Supervisor（主 Agent）**：把模糊任务拆解为结构化 `InvestigationCard[]`（调查卡：目标、关键问题、证据要求、建议工具、优先级）。
2. **Investigator（子 Agent）**：针对每张调查卡运行受控 Loop——依据工具结果提出假设、调用授权工具、识别证据缺口、动态决定继续下钻或停止。
3. **Reviewer（独立评审）**：检查证据充分性、反例、样本偏差、结论越界；决定"通过"或"要求一次受控补查"（补查全任务仅允许一次）。
4. **硬预算**：最大子任务数、最大 Loop 轮数、最大工具调用数、阶段/单次调用超时。
5. **明确停止原因**：证据充分 / 数据不足 / 预算耗尽 / 工具失败 / 非法输出。
6. **真实 Function Calling**：Investigator 通过标准 OpenAI 工具调用协议驱动工具选择（方案 A，已确认）。

### 1.2 非目标（V0.4 不做）

- Agent 框架（LangGraph/AutoGen）、跨任务长期记忆；沿用 V0 原则不引入。
- 动态生成任意 Skill / 工具；只从预定义工具白名单内选择。
- 崩溃后自动恢复、任务优先级队列、多用户权限。
- 完整审计可视化工作台（V0.5），本轮前端只做主计划/子任务进度/停止原因。
- 全量向量化、复杂主题聚类、知识图谱、因果归因。

---

## 2. 架构设计

### 2.1 模块结构

```
app/
  agent/                        # NEW: V0.4 三角色 Agent
    __init__.py
    protocols.py               # InvestigationCard / InvestigatorTurn / StopReason / 评审协议的数据结构
    budgets.py                 # Budget / BudgetCounter（硬预算计数与超限判定）
    tools_spec.py              # 工具 JSON Schema（供 function calling 使用）与参数解析/校验/钳制
    supervisor.py              # Supervisor 角色：意图+证据 -> InvestigationCard[]
    investigator.py            # Investigator 角色：单卡受控 Loop（含 function calling）
    reviewer.py                # Reviewer 角色：证据评审 -> pass / request_supplement
    orchestrator.py            # 运行编排：Supervisor -> Investigator[] -> Reviewer -> 综合报告
  llm/
    provider.py                # MODIFY: 支持 tools 参数（function calling）与 tool_calls 解析
  config.py                    # MODIFY: 新增 V0.4 配置（预算、开关）
  api/                         # MODIFY: 接受 max_subtasks/max_tool_calls、启动 agent 引擎
  workflow/                    # 保留 V0.3 WorkflowEngine（ENABLE_WORKFLOW_ENGINE 路径，不动）

skills/                       # V0.3 Skill YAML 保留不动（V0.4 不走 stage 引擎）
```

### 2.2 运行流程（Orchestrator 主循环）

```text
task_created
  -> supervisor_planning        Supervisor 产出 InvestigationCard[] + 全局预算说明
  -> reviewer_precheck（可选）    V0.4 简化：评审在调查后统一执行，不设前置预评审
  -> for card in cards:
        investigator_run        单卡 Loop（function calling，受预算约束）-> InvestigatorResult
                                （含 stop_reason、本轮证据、发现的假设/判断）
  -> reviewer_review            汇总证据 -> 通过 或 要求一次受控补查
        （若要求补查且未超过 1 次：分配新卡 -> investigator 执行 -> 再次 review）
  -> strategy_synthesis         综合报告（区分 事实/解释性判断/待验证假设 三层）
  -> task_finished
```

### 2.3 三个逻辑角色

三个角色共用同一个真实 LLM 模型，通过 **不同的 Prompt、工具白名单、上下文与输出协议** 形成逻辑隔离。

| 角色 | 输出协议 | 是否有工具调用 | 触发方式 |
|---|---|---|---|
| Supervisor | `InvestigationCard[]`（JSON） | 无 | 任务开始一次 |
| Investigator | `InvestigatorTurn`（JSON：工具调用 或 停止） | 有（function calling） | 每卡若干轮 |
| Reviewer | `{ verdict, issues, supplemental_query? }`（JSON） | 无 | 调查后一次（补查后再一次，至多 2 次） |

---

## 3. 核心数据结构

### 3.1 InvestigationCard（调查卡）

```python
@dataclass
class InvestigationCard:
    card_id: str
    goal_type: str                 # 该卡聚焦的目标类型（priority / anomaly / drill ...）
    title: str                     # 简短标题
    objective: str                 # 要回答的核心问题（自然语言）
    evidence_requirements: list[str]  # 需要支撑的关键证据点
    suggested_tools: list[str]     # 建议使用的工具（白名单子集，Investigator 可自选）
    priority: int                  # 1 最高
```

### 3.2 InvestigatorTurn（子 Agent 每轮的输出协议）

Investigator 每轮返回**一个回合**，二选一：

```json
{
  "type": "tool_call",
  "tool": "sample_comments",
  "arguments": {"keyword": "油耗", "limit": 10},
  "reason": "验证油耗主题评论的支持样本"
}
```

或

```json
{
  "type": "stop",
  "stop_reason": "evidence_sufficient",  // evidence_sufficient / data_insufficient / budget_exhausted
  "summary": "本卡已获得足够证据，结论如下...",
  "findings": ["..."]
}
```

### 3.3 StopReason

`evidence_sufficient` / `data_insufficient` / `budget_exhausted` / `tool_failure` / `illegal_output`。

- `budget_exhausted`：由 `BudgetCounter` 在每轮前判定（达到上限则不进入下一轮，直接以该原因停止）。
- `tool_failure`：工具执行抛错，捕获后记录事件，允许该卡以该原因停止（不崩溃整任务）。
- `illegal_output`：LLM 输出无法解析为合法回合，或调用未授权工具、参数无法校验。处理方式见 §6。

### 3.4 Reviewer 输出协议

```json
{
  "verdict": "pass" | "request_supplement",
  "issues": [
    {"type": "evidence_gap" | "counter_example" | "sample_bias" | "overclaim", "detail": "..."}
  ],
  "supplement_query": "若 request_supplement：需要补充调查的具体问题（自然语言）"
}
```

---

## 4. Function Calling 设计

### 4.1 LLMProvider 扩展

`app/llm/provider.py` 的 `chat()` 新增可选 `tools` 参数，并透传工具调用：

```python
def chat(self, messages, *, tools=None, response_format=None, temperature=None,
         max_tokens=None, timeout=None) -> LLMResult:
    body = {...}
    if tools:
        body["tools"] = tools                       # OpenAI function 定义列表
        body["tool_choice"] = "auto"
    ...
    # 解析响应时，若 message 含 tool_calls，存入 LLMResult.raw（与现状一致）
```

`LLMResult` 增加便捷读取器：`tool_calls -> list[{name, arguments(dict)}]`（从 `raw` 解析），供 Investigator 使用。**向后兼容**：不传 `tools` 时行为与现状完全一致，V0.2/V0.3 调用不受影响。

### 4.2 工具 JSON Schema

`tools_spec.py` 为注册表中 8 个工具生成 OpenAI function schema，参数类型与白名单：

| 工具 | 参数 | 说明 |
|---|---|---|
| `data_coverage` | 无 | 数据覆盖 |
| `volume_trend` | 无 | 声量趋势 |
| `period_comparison` | 无 | 周期对比 |
| `topic_frequency_tool` | 无 | 主题词频 |
| `top_sources` | `limit`(int, 1..100) | 头部来源 |
| `sample_comments` | `keyword`(str?), `limit`(int, 1..TOOL_MAX_RECORDS) | 抽样评论 |
| `drill_evidence` | `keyword`(str?), `min_like`(int?), `limit`(int, 1..TOOL_MAX_RECORDS) | 下钻 |
| `object_compare` | `other_tags`(list[str]?) | 对象比较 |

`limit` 统一钳制到 `[1, TOOL_MAX_RECORDS]`；未知工具/非法参数视为 `illegal_output`（记录 `tool_unauthorized` 事件）。

### 4.3 工具执行守卫

Investigator 调用工具时必须：
1. 工具名在 `self._allowed_tools`（该卡建议工具 ∪ 全局工具白名单）中；否则 `illegal_output`（`tool_unauthorized` 事件）。
2. 参数经 `tools_spec` 校验/钳制后传给工具函数 `GetTool(name)(ds, snap, store, **params)`。
3. 工具结果登记证据（工具内部已完成），Investigator 将返回摘要注入下一轮上下文。
4. 发射 `tool_call` / `subtask_tool` 审计事件。

---

## 5. 预算控制

### 5.1 配置项（app/core/config.py 新增）

```python
ENABLE_AGENT_ENGINE: bool = Field(default=True)   # 主开关：true 走 V0.4 Agent
AGENT_MAX_SUBTASKS: int = Field(default=6)        # 最大子任务（调查卡）数
AGENT_MAX_LOOPS: int = Field(default=3)           # 每卡最大 Loop 轮数
AGENT_MAX_TOOL_CALLS: int = Field(default=20)     # 全任务最大工具调用总数
AGENT_LLM_TIMEOUT_MS: int = Field(default=300000) # 单次 LLM 调用超时
AGENT_MAX_SUPPLEMENTS: int = Field(default=1)     # 最大受控补查次数
```

`.env.example` 同步追加说明性示例（不填真实值）。

### 5.2 BudgetCounter

`budgets.py` 提供 `Budget`（上限集合）+ `BudgetCounter`（运行时累计）：

- `check_subtask_budget()` / `check_loop_budget()` / `check_tool_calls()`：分别判断是否达上限。
- 超限时 `Investigator` 以 `budget_exhausted` 停止（记录事件），父循环不再派发新卡。
- 预算为**硬限制**：每轮开始前检查，超过即停止，绝不静默解锁。

### 5.3 请求级覆盖

`POST /api/tasks` 现状已接受可选 `max_subtasks` / `max_tool_calls`（默认 `None`）。V0.4 在 `CreateTaskRequest` 基础上启用：非 None 时覆盖对应上限，否则用配置默认值。整个引擎不接受无限预算。

---

## 6. 非法输出与错误处理

1. **LLM 输出非 JSON / 非 dict**：重试一次（`illegal_output` 记录，重新要求输出合法 JSON）；仍失败则任务以失败结束，保留错误，不生成伪报告。
2. **未知工具 / 未授权工具**：发射 `tool_unauthorized`，该轮按 `illegal_output` 处理（不再执行任何工具），本卡停止。
3. **工具执行失败**：捕获异常，发射 `tool_failure` 事件，该卡以 `tool_failure` 停止；task 不整体失败，交给 Reviewer 与 supervisor 判断。
4. **参数非法**：按 `tools_spec` 校验不通过则视为 `illegal_output`（不执行工具）。
5. **评审不通过且无补查额度**：报告标记该卡"评审未通过"，保留事实层与假设层，不硬造结论。

所有错误路径均显式记录审计事件（`task_failed` / `agent_error` / `stop_reason`），**不静默吞错**。

---

## 7. 报告结构

V0.4 报告在 V0.2/V0.3 基础上扩展，明确区分三层，同时保持与现有 `ReportView` 渲染兼容（字段沿用 `scope / overall / themes / ...`）：

```json
{
  "scope": {...},
  "overall": {...},
  "themes": [...],
  "sources": [...],
  "risk_opportunity": [...],
  "evidence_gaps": [...],
  "actions": [...],
  "metrics": [...],
  "agent": {
    "cards": [InvestigationCard...],
    "subtask_results": [...],          // 每卡的结果与 stop_reason
    "review": {"verdict": "pass", "issues": [...]},
    "budget": {"subtasks_used": 3, "tool_calls_used": 12, "loops_used": 8}
  }
}
```

"事实 / 解释性判断 / 待验证假设"三层：
- `themes`（主题）与 `sources` 等统计字段 = **事实层**（来自确定性工具，可追溯到证据）。
- `risk_opportunity` = **解释性判断层**（登记为 `judgment` 证据类型，引用证据 ID）。
- `evidence_gaps` / 部分 `risk_opportunity` 中的不确定项 = **待验证假设层**（登记为 `assumption`）。

---

## 8. 前端（最小范围）

在现有 `Timeline.jsx` / `App.jsx` 基础上，将运行过程分组展示（V0.4 任务）：

1. **主计划**：Supervisor 输出的调查卡列表（卡片标题 + 目标）。
2. **子任务进度**：每张调查卡的状态（running / done / stopped）、stop_reason、工具调用摘要。
3. **停止原因**：每卡与总任务明确显示（如 `budget_exhausted` / `data_insufficient`）。

数据来源：扩展 `GET /api/tasks/{task_id}/timeline` 或复用现有事件流，由前端按 `event_type`（新增 `agent_plan` / `subtask_start` / `subtask_stop` / `review_result` / `tool_call`）归类渲染。不做 V0.5 的完整审计工作台（结论—证据双向查看等留到 V0.5）。运行中任务沿用 2.5s 轮询。

---

## 9. 兼容性与路径切换

| 执行路径 | 开关 | 复用 |
|---|---|---|
| V0.2 基线 | `ENABLE_WORKFLOW_ENGINE=false` | `app/pipeline/` 不动 |
| V0.3 分阶段 | `ENABLE_WORKFLOW_ENGINE=true AND ENABLE_AGENT_ENGINE=false` | `app/workflow/` 不动 |
| V0.4 Agent | `ENABLE_AGENT_ENGINE=true`（默认） | 新增 `app/agent/` |

证明 V0.2 基线任务仍可运行（验收标准 7）：保留 `ENABLE_AGENT_ENGINE` / `ENABLE_WORKFLOW_ENGINE` 组合切换，V0.2 基线可用 `ENABLE_WORKFLOW_ENGINE=false` 复跑。

后端保持模块化单体：新增 `app/agent/`，**不改动** `app/workflow/`、`app/pipeline/`、`app/skill/` 的既有对外接口。

---

## 10. 测试策略

### 10.1 单元/集成测试（mock LLM + FakeDS，不真实调用）

- `test_tools_spec.py`：工具 schema 生成、参数校验/钳制、未知工具拒绝。
- `test_budgets.py`：Budget/BudgetCounter 上限判定与超限停止。
- `test_supervisor.py`：意图+证据 -> InvestigationCard[]，结构化解析。
- `test_investigator.py`：单卡 Loop——首轮 tool_call、结果回注、继续下钻或停止（`evidence_sufficient` / `data_insufficient`）、每轮前预算检查、工具失败/非法输出处理。
- `test_reviewer.py`：pass / request_supplement、评审问题分类。
- `test_orchestrator.py`：完整任务（mock LLM + FakeDS），验证卡片生成、子任务执行、评审、补查（至多 1 次）、三层报告、审计事件、预算累计。
- `test_agent_api.py`：API 接受 max_subtasks 覆盖、启动 agent 引擎。

### 10.2 真实 LLM 冒烟（手动标记）

- `test_v04_smoke.py`（`@pytest.mark.manual`）：真实 LLM + 真实 MySQL，跑一个真实任务，验证至少一次动态下钻（首个 `tool_call` 后 Investigator 依据结果发起新的下钻查询）与报告生成。

### 10.3 验收标准对照

| 标准 | 落点 |
|---|---|
| 主 Agent 生成多个调查卡 | Supervisor（test_supervisor） |
| 子 Agent 依据首轮结果动态下钻 | Investigator loop（test_investigator / 冒烟） |
| 工具调用授权且不超预算 | §4.3 + BudgetCounter（test_budgets / test_investigator） |
| 明确停止 | StopReason（test_investigator） |
| 评审指出缺口并决定补查 | Reviewer（test_reviewer） |
| 报告区分三层 | §7（test_orchestrator） |
| V0.2 基线可复跑 | §9（test_api V0.2 兼容回归） |

---

## 附录：关键设计决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 工具调用方式 | 真实 Function Calling（方案 A） | 验证"子 Agent 依据工具结果决定下一步"这一 V0.4 核心价值；模型已确认支持 |
| 预算来源 | 配置默认 + 请求级可选覆盖 | 满足"输入含探索预算"，同时有兜底硬限制 |
| 评审时机 | 调查后统一评审 | V0.4 简化，评审聚焦"证据/反例/偏差"，不做前置预评审 |
| 补查执行者 | Investigator（Reviewer 只决策不调工具） | 单一工具调用面，评审保持"把关"角色 |
| V0.3 去留 | 保留，路径切换 | 验收标准 7 要求 V0.2 可复跑；V0.3 是已验收里程碑，不过度重构 |
| 报告结构 | 扩展兼容现有字段 + 新增 agent 块 | 前端 ReportView 最小改动即可渲染；保留三层区分能力 |
```
