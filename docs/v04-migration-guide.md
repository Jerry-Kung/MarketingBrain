# V0.4 迁移指南

## 概述

V0.4 引入「主 Agent 与受控子 Agent Loop」：在 V0.3 分阶段工作流之上，用三个角色（主 Agent 规划、子 Agent 受控下钻、评审把关）让 Agent 在**确定性骨架内**自主探索。本文档说明如何从 V0.3 迁移到 V0.4，如何在两者间切换与回退。

## 破坏性变更

**无**。V0.4 完全向下兼容 V0.3 与 V0.2。三个引擎（V0.2 基线 / V0.3 工作流 / V0.4 Agent）并存，通过开关切换。

## 新增功能

1. **三角色 Agent Loop**：Supervisor（规划调查卡）→ Investigator（受控下钻）→ Reviewer（把关评审）
2. **真 Function Calling**：子 Agent 用 LLM 原生 `tools` / `tool_choice=auto` 调用白名单工具
3. **预算硬限制**：子任务/单卡循环/工具调用/补查四维上限，超限即停
4. **停止原因**：`evidence_sufficient` / `data_insufficient` / `budget_exhausted` / `tool_failure` / `illegal_output`
5. **三层报告**：事实层 + 解释性判断 + 待验证假设
6. **前端 V0.4 视图**：任务详情额外展示调查计划 / 子任务进度 / 停止原因 / 评审结论 / 预算计数

## 迁移步骤

### 1. 配置扩展

在 `.env` 中追加（可选，默认值已足够）：

```ini
# ---------- V0.4 Agent 配置 ----------
ENABLE_AGENT_ENGINE=true
AGENT_MAX_SUBTASKS=6
AGENT_MAX_LOOPS=3
AGENT_MAX_TOOL_CALLS=20
AGENT_MAX_SUPPLEMENTS=1
AGENT_LLM_TIMEOUT_MS=300000
```

`.env.example` 中已含示例（不含真实凭证）。

### 2. 启用 V0.4 Agent

默认已启用（`ENABLE_AGENT_ENGINE=true`、`ENABLE_WORKFLOW_ENGINE=true`）。`POST /api/tasks` 的路由优先级为 Agent → Workflow → V0.2，因此新任务默认走 V0.4 Agent。

### 3. 前端验证

任务详情页：V0.4 任务（`result.agent` 存在）额外渲染「调查计划 / 子任务进度 / 停止原因」视图；V0.2/V0.3 任务不受影响。

### 4. 验证集成测试

```bash
pytest tests/test_agent_integration.py -v
```

真实 LLM + MySQL 冒烟：

```bash
pytest tests/test_v04_smoke.py -v -m manual
```

## 回退到 V0.3 / V0.2

- **回退到 V0.3**：设置 `ENABLE_AGENT_ENGINE=false`，保留 `ENABLE_WORKFLOW_ENGINE=true`。新任务走 V0.3 工作流。
- **回退到 V0.2**：设置 `ENABLE_AGENT_ENGINE=false` 且 `ENABLE_WORKFLOW_ENGINE=false`。新任务走 V0.2 基线流水线。

## 常见问题

**Q: V0.2 / V0.3 已有任务会被影响吗？**

A: 不会。各引擎历史任务的 `result` / `skill_name` 独立，前端按 `result.agent` 是否存在决定是否渲染 V0.4 视图。

**Q: 子 Agent 会执行任意 SQL 或越权工具吗？**

A: 不会。数据源仍只读（预定义方法 + 只读会话）；工具名必须落在 `TOOL_WHITELIST`，参数经 `validate_and_coerce` 钳制，越界记 `tool_unauthorized` 并停止该卡。

**Q: 预算耗尽会怎样？**

A: 当前子任务以 `budget_exhausted` 正常停止并记录，Orchestrator 继续走评审与报告组装，不会丢弃已得结果。

**Q: LLM 输出非法会怎样？**

A: 该卡重试一次（记录 `illegal_output`），仍非法则以此卡 `illegal_output` 停止，不无限重试。
