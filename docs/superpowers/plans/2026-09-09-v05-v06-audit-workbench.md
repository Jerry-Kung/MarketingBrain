# V0.5 + V0.6 审计工作台实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 V0.5 完整审计可视化工作台（任务列表/筛选、运行审计主子任务树、工具输入/返回摘要、策略包报告、结论↔证据双向反查、原始 JSON 查看），并补齐 V0.6 可编码部分（一次性 LLM 报告种子基线、评分表/回归集/验收模板）。

**Architecture:** 前端纯消费、后端只做两处小增强（`subtask_tool` 事件补记 `result_summary`/`bias_note`；`CreateTaskRequest` 增加可选 `mode` 并接入的最小 `oneshot` 流水线）。三模式（V0.2 固定流程 / V0.3 工作流 / V0.4 Agent / V0.6 一次性报告）并存，通过开关与 `mode` 切换，互不侵入。

**Tech Stack:** React 19 + Vite（前端），Vitest + @testing-library/react（前端单测）；Python + FastAPI + Pydantic（后端），Pytest（后端）。

**Spec:** `docs/superpowers/specs/2026-09-09-v05-v06-audit-workbench-design.md`

## Global Constraints

- 只做满足验收标准的最小且完整修改；不引入 UI 组件库、状态管理框架、路由库。
- 前端现有组件（Timeline.jsx、AgentPlan.jsx）保持不动；App.jsx 仅按职责拆出视图组件，不改变现有视图逻辑与产物。
- 后端不改 Agent 内核（supervisor/investigator/reviewer/orchestrator 的决策/预算/协议逻辑），只在其事件记录处补记摘要。
- `subtask_tool` 事件新增字段 `result_summary`（紧凑可读摘要，非全量返回 dict）与 `bias_note`。
- `mode` 取值仅 `Literal["oneshot", "agent", "workflow", "baseline"]`；未指定 `mode` 时沿用现有 `ENABLE_AGENT_ENGINE`/`ENABLE_WORKFLOW_ENGINE` 开关，保持向后兼容（现有既有测试不破坏）。
- oneshot 复用 `BaselinePipeline`（确定性取数），报告由单次 LLM 生成（不校验、不分阶段、无子 Agent 下钻）；结果 `result["mode"] == "oneshot"`。
- 旧事件（无 `result_summary`/`bias_note`）前端应优雅降级为仅显示 arguments，不报错。
- 文档写入 `docs/`，统一用简体中文；涉及中文字符写入后检查编码。

---

## 文件结构

**后端（改动 3 文件 + 新增 1 文件 + 测试）：**
- `app/agent/investigator.py` — 新增 `_result_summary` 辅助函数；`subtask_tool` 事件补记 `result_summary`/`bias_note`。
- `tests/test_investigator.py` — 新增断言 `subtask_tool` 事件含摘要字段的用例。
- `app/pipeline/oneshot.py`（新增）— 最小一次性 LLM 报告流水线，复用 `BaselinePipeline` + `generate_strategy_pack`。
- `app/api/schemas.py` — `CreateTaskRequest` 增加 `mode: Optional[Literal[...]]`。
- `app/api/routes.py` — `create_task` 增加 `mode="oneshot"` 分支；`_task_to_response` 透传 `mode`。
- `tests/test_api_oneshot.py`（新增）— oneshot 路由集成测试。

**前端（新增 5 文件 + 调整 App.jsx）：**
- `page/src/AuditWorkbench.jsx` + `.css` — 运行审计视图（主子任务树 + 工具输入/返回摘要 + 停止原因）。
- `page/src/ReportView.jsx` — 策略包报告（从 App.jsx 现有 `ReportView`/`RenderReport`/`RenderValue`/`ResolveRefs` 抽出，含证据双向反查）。
- `page/src/EvidencePanel.jsx` — 证据→判断反向面板（点证据看被哪些判断引用）。
- `page/src/RawJson.jsx` — 任务结果原始 JSON 折叠查看。
- `page/src/App.jsx` — 拆分：把现有报告/工具视图逻辑移到 `ReportView.jsx`，保留 App 壳、数据概览、任务创建、历史列表、筛选；接入 `AuditWorkbench`/`RawJson`。
- `page/src/App.css` — 补充新视图所需样式。
- `page/tests/`（新增）— Vitest 组件单测（主子任务树构建、证据双向映射、降级处理）；`page/vitest.config.js`、`page/package.json` 增依赖与脚本。

---

### Task 1: `subtask_tool` 事件补记工具返回摘要

**Files:**
- Modify: `app/agent/investigator.py`（新增 `_result_summary`；修改 `_execute_tool`)
- Test: `tests/test_investigator.py`

**Interfaces:**
- Consumes: `Investigator._execute_tool(self, tool_name, arguments, task_id, card) -> dict` 内部已有 `res`（`_tool_result` 结构：`{"name", "result", "evidence_ids", "sample_size", "bias_note"}`）。
- Produces: `subtask_tool` 事件 payload 增加两个 key：`result_summary`（紧凑摘要 dict）与 `bias_note`（字符串）。前端 `AuditWorkbench` 消费。

- [ ] **Step 1: 写失败测试**

在 `tests/test_investigator.py` 的 `TestInvestigator` 中新增用例，断言 `subtask_tool` 事件 payload 含 `result_summary` 与 `bias_note`，且 `result_summary["sample_size"] == 5`（与工具 `sample_comments` 的 `sample_size` 一致）：

```python
def test_subtask_tool_event_has_result_summary_and_bias_note(self):
    """V0.5 回归：subtask_tool 事件须补记工具返回摘要与偏差提示。"""
    llm = FakeLLM([
        {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
        ]}}]}},
        {"content": json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "ok"})},
    ])
    inv, event_repo, store, counter = _setup(llm)
    result = inv.run(_card(), "t1", 1)
    assert result.stop_reason == "evidence_sufficient"
    events = event_repo.get_events("t1")
    tool_events = [e for e in events if e.event_type == "subtask_tool"]
    assert tool_events, "应有 subtask_tool 事件"
    payload = tool_events[0].payload
    assert "result_summary" in payload, "subtask_tool 应记录 result_summary"
    assert payload["result_summary"].get("sample_size") == 5
    assert "bias_note" in payload, "subtask_tool 应记录 bias_note"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_investigator.py::TestInvestigator::test_subtask_tool_event_has_result_summary_and_bias_note -v`
Expected: FAIL，`tool_events[0].payload` 缺 `result_summary` key（当前事件只记 `card_id`/`tool`/`arguments`/`sample_size`）。

- [ ] **Step 3: 实现 `_result_summary` 与事件补记**

在 `app/agent/investigator.py` 增加辅助函数（放在 `_stringify_findings` 之后）：

```python
def _result_summary(res: dict) -> dict:
    """把工具返回结果压缩为紧凑可读摘要，避免审计事件膨胀。

    各工具的 result dict 形如：
    - data_coverage: {"comment_count", "datasource"}
    - volume_trend: {"total"}
    - period_comparison: {"current", "previous", "change_rate"}
    - topic_frequency_tool: {"topics": [{"topic", "comment_count"}]}
    - top_sources: {"videos": [{"job_id", "video_title", "comment_count", ...}]}
    - sample_comments / drill_evidence: {"comments": [...]}
    - object_compare: {"current", "other", "change_rate"}

    逐字段抽取，list 最多保留前 3 项（每项仅保留 title/job_id/count 等标量），
    缺失/非标量安全回退为 sample_size 计数，保证事件体量可控。
    """
    result = res.get("result")
    if not isinstance(result, dict):
        return {"sample_size": res.get("sample_size", 0)}
    summary = {}

    def _take(key):
        v = result.get(key)
        if v is not None:
            summary[key] = v

    for key in ("comment_count", "datasource", "total", "current", "previous",
                "change_rate", "sample_size"):
        _take(key)

    if isinstance(result.get("topics"), list):
        summary["topics"] = [
            {"topic": t.get("topic"), "comment_count": t.get("comment_count")}
            for t in result["topics"][:3] if isinstance(t, dict)
        ]
        summary["topic_count"] = len(result["topics"])

    if isinstance(result.get("videos"), list):
        summary["videos"] = [
            {"job_id": v.get("job_id"), "video_title": v.get("video_title"),
             "comment_count": v.get("comment_count")}
            for v in result["videos"][:3] if isinstance(v, dict)
        ]
        summary["video_count"] = len(result["videos"])

    if isinstance(result.get("comments"), list):
        summary["comment_count"] = len(result["comments"])

    if "comment_count" not in summary:
        summary["sample_size"] = res.get("sample_size", 0)
    return summary
```

修改 `_execute_tool` 中的 `subtask_tool` 事件记录（当前代码见 `app/agent/investigator.py:240`）：

```python
self.event_repo.append_event(task_id, "subtask_tool", {
    "card_id": card.card_id, "tool": tool_name, "arguments": coerced,
    "sample_size": res.get("sample_size", 0),
    "result_summary": _result_summary(res),
    "bias_note": res.get("bias_note", ""),
})
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_investigator.py -v`
Expected: PASS（含既有用例：none 破坏）。

- [ ] **Step 5: 提交**

```bash
git add app/agent/investigator.py tests/test_investigator.py
git commit -m "feat(agent): subtask_tool 事件补记工具返回摘要与偏差提示"
```

---

### Task 2: 一次性 LLM 报告种子基线（oneshot 流水线）

**Files:**
- Create: `app/pipeline/oneshot.py`
- Test: `tests/test_api_oneshot.py`（新增）

**Interfaces:**
- Consumes: `app.pipeline.baseline.BaselinePipeline(datasource, snapshot, evidence_store).run() -> AnalysisBundle`；`app.llm.report.generate_strategy_pack(bundle, provider, intent) -> (report_dict, llm_result)`。
- Produces: `run_oneshot_sync(task_id, *, task_repo, event_repo, datasource, llm_provider, settings) -> dict`。返回 `result` 含 `report`/`evidence`/`meta`/`mode:`"oneshot"`/`progress`。

- [ ] **Step 1: 写失败测试**

```python
"""V0.6 oneshot 模式：一次性 LLM 报告流水线的 API 路由集成测试（同步）。"""
import os, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient


class FakeData:
    def count_comments(self, **kw): return 100
    def time_series(self, **kw):
        return {"buckets": [{"start": "2026-08-01", "count": 100}], "total": 100}
    def top_videos(self, **kw):
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 10}]
    def topic_frequency(self, **kw):
        return [{"topic": "油耗", "comment_count": 40}]
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(comment_id="c1", video_title="坦克300", job_id="j1", content="评论1"),
                CommentRecord(comment_id="c2", video_title="坦克300", job_id="j1", content="评论2")]
    def data_overview(self):
        from app.datasource.adapter import DataOverview
        return DataOverview(job_count=100, comment_count=100, start_time=None, end_time=None)


class MockLLM:
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMUsage, LLMResult
        payload = {"scope": {"comment_count": 100}, "themes": [{"theme": "油耗"}]}
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        return payload, LLMResult(json.dumps(payload), usage=usage)


def _settings(**kw):
    from app.core.config import Settings
    base = dict(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                ENABLE_WORKFLOW_ENGINE=True, ENABLE_AGENT_ENGINE=True)
    base.update(kw)
    return Settings(**base)


def test_create_task_with_mode_oneshot(tmp_path):
    from app.api.routes import create_app
    app = create_app(
        db_path=os.path.join(tmp_path, "app_state.db"),
        datasource=FakeData(), llm_provider=MockLLM(),
        settings_override=_settings(), background=True, require_sync=True,
    )
    client = TestClient(app)
    resp = client.post("/api/tasks", json={"raw_input": "分析坦克300", "mode": "oneshot"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["mode"] == "oneshot"
    result = data.get("result") or {}
    assert result.get("mode") == "oneshot"
    assert result.get("report", {}).get("scope", {}).get("comment_count") == 100
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_api_oneshot.py -v`
Expected: FAIL — `TaskResponse`/`create_task` 尚无 `mode` 字段，且 `mode="oneshot"` 未接入路由（会走 Agent 分支导致 LLM 响应格式不匹配）。

- [ ] **Step 3: 实现 oneshot 流水线**

创建 `app/pipeline/oneshot.py`：

```python
"""V0.6 一次性 LLM 报告流水线（种子基线）。

与 V0.2 固定流程共用 BaselinePipeline 的确定性取数，但报告由**单次** LLM
调用生成（不分阶段、无子 Agent 下钻、不校验），构成「非 Agent 非固定」的
对照基线，用于 V0.6 三模式（一次性 / 固定流程 / 受控 Agent）对比评测。
"""
import threading

from app.pipeline.baseline import BaselinePipeline
from app.llm.report import generate_strategy_pack


def run_oneshot_sync(task_id, *, task_repo, event_repo, datasource,
                     llm_provider, settings=None):
    """同步执行一次 oneshot 任务，返回 result。出错则标记 failed。"""
    try:
        task = task_repo.get_task(task_id)
        if task is not None and task.status in {"success", "failed"}:
            return {"already": task.status}

        from app.snapshot.snapshot import LogicalSnapshot
        from app.store.evidence import EvidenceStore
        snapshot = LogicalSnapshot.from_dict(task.snapshot)

        task_repo.update_status(task_id, "running")
        event_repo.append_event(task_id, "task_started", {"task_id": task_id})

        evidence_store = EvidenceStore(task_id)
        pipeline = BaselinePipeline(datasource, snapshot, evidence_store)
        event_repo.append_event(task_id, "pipeline_start", {"stage": "oneshot"})
        bundle = pipeline.run()

        event_repo.append_event(task_id, "report_start", {})
        report, llm_result = generate_strategy_pack(bundle, llm_provider, intent=task.parsed_intent)
        event_repo.append_event(task_id, "report_done", {"usage": llm_result.usage.to_dict()})

        evidence_list = evidence_store.to_dicts()
        result = {
            "report": report,
            "evidence": evidence_list,
            "meta": {
                "model": llm_result.usage.model,
                "prompt_tokens": llm_result.usage.prompt_tokens,
                "completion_tokens": llm_result.usage.completion_tokens,
                "total_tokens": llm_result.usage.total_tokens,
                "latency_ms": llm_result.usage.latency_ms,
            },
            "mode": "oneshot",
            "progress": {"stage": "done", "tool_calls": 0, "report_success": True},
        }
        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})
        return result
    except Exception as e:
        task_repo.mark_failed(task_id, str(e))
        event_repo.append_event(task_id, "task_failed", {"error": str(e)})
        return {"error": str(e)}


def start_oneshot_background(*, task_id, task_repo, event_repo, datasource,
                             llm_provider, settings=None):
    """在新线程里执行 oneshot 任务（V0 不引入 Celery）。"""
    def _worker():
        run_oneshot_sync(
            task_id, task_repo=task_repo, event_repo=event_repo,
            datasource=datasource, llm_provider=llm_provider, settings=settings,
        )
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
```

- [ ] **Step 4: 接入路由与 schema**

> 语义约定：`mode=None` 时沿用现有开关（`use_agent`/`use_workflow`/V0.2 基线），**保持既有任务创建行为不变**（这是既有 `test_agent_api.py` 等回归的前提）。`mode="oneshot"` 强制走本 Task 新流水线。`mode="agent"/"workflow"/"baseline"` 为 V0.6 三模式对照的"建议取值"，本 Task 仅保证不报错（会分别落入现有开关逻辑），不强制改写现有路由；其中 `baseline` 对应关闭两个引擎的 V0.2 路径，由现场对照时通过配置或请求参数实现。

在 `app/api/schemas.py` 中把 `CreateTaskRequest` 增加 `mode` 字段（并 `from typing import Literal, Optional`）：

```python
from typing import Literal, Optional

class CreateTaskRequest(BaseModel):
    raw_input: str = Field(
        min_length=1,
        description="用户的自然语言分析问题，如：分析坦克300近期的舆情变化",
    )
    max_subtasks: Optional[int] = Field(default=None, ge=1, description="最大子任务数")
    max_tool_calls: Optional[int] = Field(default=None, ge=1, description="最大工具调用数")
    # V0.6 对照模式：显式指定则覆盖开关；None 时沿用 ENABLE_AGENT_ENGINE/WORKFLOW 默认路由
    mode: Optional[Literal["oneshot", "agent", "workflow", "baseline"]] = None
```

在 `app/api/routes.py` 的 `TaskResponse` 定义处增加 `mode` 字段：

```python
class TaskResponse(BaseModel):
    task_id: str
    status: str
    raw_input: str
    parsed_intent: dict
    snapshot: dict
    skill_name: Optional[str] = None
    created_at: str
    updated_at: str
    result: Optional[dict] = None
    error: Optional[str] = None
    mode: Optional[str] = None  # V0.6 对照模式
```

在 `_task_to_response` 增加一行透传：`mode=task.result.get("mode") if task.result else None`。

在 `create_task` 中（`use_agent` 分支之前）加 oneshot 分支（放在 `use_agent = ...` 计算之后、`if llm_provider is None:` 检查之后的原 `task_repo.create_task` 与 `if _ds is None` 之后即可）。**注意：`mode` 显式为 `"oneshot"` 时无论开关如何都走 oneshot。** 在路由中新增：

```python
        if req.mode == "oneshot":
            if require_sync:
                from app.pipeline.oneshot import run_oneshot_sync
                run_oneshot_sync(
                    task.task_id, task_repo=task_repo, event_repo=event_repo,
                    datasource=_ds, llm_provider=llm_provider, settings=settings,
                )
                task = task_repo.get_task(task.task_id)
            else:
                from app.pipeline.oneshot import start_oneshot_background
                start_oneshot_background(
                    task.task_id, task_repo=task_repo, event_repo=event_repo,
                    datasource=_ds, llm_provider=llm_provider, settings=settings,
                )
            return _task_to_response(task)
```

`TaskResponse` schemas 在 `app/api/schemas.py` 也加 `mode` 字段，确保 `response_model` 校验通过。

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_api_oneshot.py -v && pytest tests/test_api.py tests/test_api_v02.py tests/test_agent_api.py -v`
Expected: 全 PASS（oneshot 通过；既有 TaskResponse 缺 `mode` 字段不报错——加的是 Optional）。

- [ ] **Step 6: 提交**

```bash
git add app/pipeline/oneshot.py app/api/schemas.py app/api/routes.py tests/test_api_oneshot.py
git commit -m "feat(pipeline): 一次性 LLM 报告种子基线（V0.6 oneshot 模式）"
```

---

### Task 3: 前端审计工作台视图组件拆分

**Files:**
- Create: `page/src/AuditWorkbench.jsx`、`page/src/AuditWorkbench.css`
- Create: `page/src/RawJson.jsx`
- Create: `page/src/ReportView.jsx`（从 App.jsx 的 `ReportView`/`RenderReport`/`RenderValue`/`ResolveRefs`/`FieldLabel` 抽离）
- Create: `page/src/EvidencePanel.jsx`
- Modify: `page/src/App.jsx`（移除内联的 `ReportView` 等，改为 import 新组件；新增任务筛选与审计工作台/RawJson 接入）
- Modify: `page/src/App.css`（补充新视图所需样式）

**Interfaces:**
- Consumes: `/api/tasks?task_id=` 返回的 task（含 `result`、`result.agent`、`result.mode`）；`/api/tasks/{task_id}/timeline` 的事件流；`/api/tasks/{task_id}/evidence` 的证据列表；`/api/tasks/{task_id}/report` 的报告。
- Produces:
  - `AuditWorkbench({ timeline })` — 主子任务树 + 工具输入/返回摘要 + 停止原因。从 `/timeline` 事件构建 `{cards:[{card_id,title,stop_reason, tools:[{tool,arguments,result_summary,bias_note}]}]}`。
  - `ReportView({ task, result })` — 策略包报告，含结论→证据（`evidence_refs`）反查。
  - `EvidencePanel({ evidence, referencedBy })` — 证据→判断（`referenced_by`）反查。
  - `RawJson({ json })` — 折叠展示原始 JSON（含失败任务 error）。

- [ ] **Step 1: 写失败测试（前端单测）**

新建 `page/tests/auditWorkbench.test.jsx`（用 Vitest，测试 `buildTree` 纯函数）。先装前端测试依赖：

```bash
cd page && npm install --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom
```

测试：主子任务树构建逻辑（从 timeline 事件卡分组、停止原因、工具摘要字段降级）。

```jsx
import { describe, it, expect } from 'vitest'
import { buildTree } from '../src/AuditWorkbench'

describe('AuditWorkbench buildTree', () => {
  it('按卡分组工具调用并提取停止原因', () => {
    const events = [
      { event_type: 'subtask_start', payload: { card_id: 'c1', title: '主题' } },
      { event_type: 'subtask_tool', payload: { card_id: 'c1', tool: 'topic_frequency_tool', arguments: {}, result_summary: { topic_count: 3 }, bias_note: 'b' } },
      { event_type: 'subtask_stop', payload: { card_id: 'c1', stop_reason: 'evidence_sufficient' } },
    ]
    const tree = buildTree(events)
    expect(tree).toHaveLength(1)
    expect(tree[0].tools).toHaveLength(1)
    expect(tree[0].tools[0].result_summary.topic_count).toBe(3)
    expect(tree[0].stop_reason).toBe('evidence_sufficient')
  })

  it('旧事件缺 result_summary 时优雅降级', () => {
    const events = [
      { event_type: 'subtask_start', payload: { card_id: 'c1', title: 't' } },
      { event_type: 'subtask_tool', payload: { card_id: 'c1', tool: 'sample_comments', arguments: { limit: 5 } } },
      { event_type: 'subtask_stop', payload: { card_id: 'c1', stop_reason: 'evidence_sufficient' } },
    ]
    const tree = buildTree(events)
    expect(tree[0].tools[0].result_summary).toBeUndefined()
  })
})
```

新建 `page/vitest.config.js`（不覆盖现有测试配置，Vite React 插件复用）：

```js
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/**/*.test.{jsx,js}'],
  },
})
```

在 `page/package.json` 的 `scripts` 增加：`"test": "vitest run"`。

- [ ] **Step 2: 运行确认失败**

Run: `cd page && npx vitest run tests/auditWorkbench.test.jsx`
Expected: FAIL — `AuditWorkbench` 组件与 `buildTree` 不存在。

- [ ] **Step 3: 实现前端组件**

创建 `page/src/AuditWorkbench.jsx`（主子任务树 + 工具输入/返回摘要 + 停止原因，**自取 `/timeline`**）：

```jsx
import { useEffect, useState } from 'react'
import './AuditWorkbench.css'

// 纯函数：从 /timeline 事件构建主子任务树。
// 旧事件（V0.4 之前）缺 result_summary/bias_note 时优雅降级（undefined），不报错。
function buildTree(events) {
  const cards = []
  for (const ev of events) {
    if (ev.event_type === 'subtask_start') {
      cards.push({ card_id: ev.payload.card_id, title: ev.payload.title, tools: [], stop_reason: null, summary: '' })
    } else if (ev.event_type === 'subtask_tool') {
      const card = cards.find((c) => c.card_id === ev.payload.card_id)
      if (card) card.tools.push({
        tool: ev.payload.tool,
        arguments: ev.payload.arguments || {},
        result_summary: ev.payload.result_summary,
        bias_note: ev.payload.bias_note,
      })
    } else if (ev.event_type === 'subtask_stop') {
      const card = cards.find((c) => c.card_id === ev.payload.card_id)
      if (card) {
        card.stop_reason = ev.payload.stop_reason
        card.summary = ev.payload.summary || ''
      }
    }
  }
  return cards
}

// 组件：接收 taskId，自取 /api/tasks/{taskId}/timeline（与 <Timeline taskId> 各自独立取数）。
export default function AuditWorkbench({ taskId }) {
  const [timeline, setTimeline] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!taskId) return
    let alive = true
    fetch(`/api/tasks/${taskId}/timeline`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => { if (alive) setTimeline(data) })
      .catch((e) => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [taskId])

  if (error) return <div className="audit-empty">审计数据加载失败：{error}</div>
  if (!timeline || !timeline.events || timeline.events.length === 0) {
    return <div className="audit-empty">暂无子任务记录</div>
  }

  const cards = buildTree(timeline.events)
  if (cards.length === 0) return <div className="audit-empty">无子任务记录</div>

  return (
    <div className="audit-workbench">
      <h3>审计工作台</h3>
      {cards.map((card, i) => (
        <div key={i} className="audit-card">
          <div className="audit-card-head">
            <span className="audit-card-title">{card.title}</span>
            {card.stop_reason && (
              <span className="audit-stop">停止：{stopLabel(card.stop_reason)}</span>
            )}
          </div>
          {card.summary && <p className="audit-summary">{card.summary}</p>}
          {card.tools.length > 0 && (
            <ul className="audit-tools">
              {card.tools.map((t, j) => (
                <li key={j} className="audit-tool">
                  <span className="audit-tool-name">{t.tool}</span>
                  <ToolDetail tool={t} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

function ToolDetail({ tool }) {
  return (
    <div className="audit-tool-detail">
      <div className="audit-tool-kv"><span className="audit-kv-label">参数</span>
        <code className="audit-kv-value">{JSON.stringify(tool.arguments)}</code></div>
      {tool.result_summary && (
        <div className="audit-tool-kv"><span className="audit-kv-label">返回</span>
          <code className="audit-kv-value">{JSON.stringify(tool.result_summary)}</code></div>
      )}
      {tool.bias_note && <p className="audit-bias">{tool.bias_note}</p>}
    </div>
  )
}

function stopLabel(reason) {
  const map = {
    evidence_sufficient: '证据充分',
    data_insufficient: '数据不足',
    budget_exhausted: '预算耗尽',
    tool_failure: '工具失败',
    illegal_output: '输出非法',
  }
  return map[reason] || reason
}

export { buildTree }
```

> 注：Step 1 的测试把 `buildTree` 作为纯函数导出测试；`AuditWorkbench` 接收 `taskId` 自取数据，与现有 `<Timeline taskId>` 的取数模式一致，互不影响。

创建 `page/src/AuditWorkbench.css`（类名以 `audit-` 前缀，避免与现有时序/卡片样式冲突）：

```css
.audit-workbench { margin-top: 1.5rem; padding: 1rem; border: 1px solid var(--border); border-radius: 8px; background: #fafafa; }
.audit-workbench h3 { margin: 0 0 .75rem; font-size: 1rem; font-weight: 600; color: var(--primary); }
.audit-empty { padding: 1rem; color: #999; font-style: italic; }
.audit-card { margin-bottom: .9rem; padding: .75rem; background: #fff; border: 1px solid var(--border); border-left: 3px solid var(--blue); border-radius: 4px; }
.audit-card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.audit-card-title { font-weight: 600; }
.audit-stop { font-size: .8rem; color: var(--amber); }
.audit-summary { margin: .4rem 0 0; font-size: .88rem; color: var(--muted); }
.audit-tools { list-style: none; margin: .5rem 0 0; padding: 0; display: flex; flex-direction: column; gap: .4rem; }
.audit-tool { padding: .5rem .6rem; background: var(--bg); border-radius: 4px; font-size: .85rem; }
.audit-tool-name { font-weight: 600; color: #333; }
.audit-tool-detail { margin-top: .3rem; }
.audit-tool-kv { display: flex; gap: 8px; }
.audit-kv-label { flex: 0 0 auto; color: var(--muted); }
.audit-kv-value { flex: 1; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.audit-bias { margin: .3rem 0 0; font-size: .8rem; color: var(--amber); }
```

创建 `page/src/RawJson.jsx`：

```jsx
import { useState } from 'react'

// 原始 JSON 折叠查看：用于失败任务定位与结果审计。
export default function RawJson({ label = '原始 JSON', json }) {
  const [open, setOpen] = useState(false)
  if (json == null) return null
  return (
    <div className="raw-json">
      <button className="raw-json-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? '收起' : '展开'} {label}
      </button>
      {open && <pre className="raw-json-pre">{JSON.stringify(json, null, 2)}</pre>}
    </div>
  )
}
```

创建 `page/src/EvidencePanel.jsx`（证据→判断反向反查）：

```jsx
// 证据反向：点击证据查看被哪些判断引用（referenced_by）。
export default function EvidencePanel({ evidence, referencedBy, onNavigate }) {
  if (!evidence || evidence.length === 0) return null
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]))
  return (
    <div className="evidence-panel">
      <h3>证据</h3>
      <ul className="evidence-list">
        {evidence.map((ev) => {
          const refs = referencedBy[ev.evidence_id] || []
          return (
            <li key={ev.evidence_id} className="evidence-item">
              <span className="evidence-kind">{ev.kind}</span>
              <span className="evidence-content">{ev.content || ev.video_title || ev.extra?.title || ev.extra?.label || ''}</span>
              {refs.length > 0 && (
                <span className="evidence-refs">被 {refs.length} 处引用</span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
```

创建 `page/src/ReportView.jsx`（从 App.jsx 抽离现有 `ReportView`/`RenderReport`/`RenderValue`/`ResolveRefs`/`FieldLabel` 逻辑，**原样搬移**，仅补充引用 `EvidencePanel` 的能力）：

```jsx
import EvidencePanel from './EvidencePanel'

// 从 App.jsx 抽离的基线报告渲染（V0.2/V0.3/V0.4/V0.6 通用）。
function ReportView({ task, result }) {
  // 搬移自 App.jsx:216-287 的 ReportView 全文，保持分支不变：
  //   - !task -> <p className="muted">加载中…</p>
  //   - status==='failed' -> 错误块
  //   - status==='running'/'pending' -> 运行中提示（含 result.progress）
  //   - !result -> <p className="muted">报告生成中或失败…</p>
  //   - 正常：meta stats + validation + RenderReport
  // 在保留原逻辑的基础上追加两处（不删除原逻辑）：
  //   {result?.mode && <span className="report-mode-tag">模式：{result.mode}</span>}
  //   <EvidencePanel evidence={result?.evidence} referencedBy={buildRefMap(result?.evidence)} />
}

// 搬移自 App.jsx:289-378 的 RenderReport/RenderValue/ResolveRefs/FieldLabel/FIELD_LABELS 原文，
// 参数签名与实现均一致（evidenceByComment/evidenceByJob 用于结论→证据反查）。
function RenderReport({ report, evidenceByComment, evidenceByJob }) { /* 原样 */ }
function RenderValue({ value, evidenceByComment, evidenceByJob }) { /* 原样 */ }
function ResolveRefs({ value, evidenceByComment, evidenceByJob }) { /* 原样 */ }
const FIELD_LABELS = { /* 原样 */ }
function FieldLabel(k) { return FIELD_LABELS[k] || k }

function buildRefMap(evidence) {
  const m = {}
  for (const ev of evidence || []) m[ev.evidence_id] = ev.referenced_by || []
  return m
}

export default ReportView
export { ReportView, buildRefMap }
```

> 执行说明：以上"原样"的代码在 `page/src/App.jsx` 中完整存在（`ReportView` L216、`RenderReport` L289、`RenderValue` L315、`ResolveRefs` L357、`FIELD_LABELS` L380、`FieldLabel` L412）。搬移时逐段复制这些既有函数体，仅在 `ReportView` 函数体末尾追加 `result.mode` 徽标与 `<EvidencePanel />` 两处，其余保持字节级一致，避免与既有基线报告渲染产生差异。

- [ ] **Step 4: 修 App.jsx 接入新组件**

修改 `page/src/App.jsx`：
1. 删除内联的 `ReportView`/`RenderReport`/`RenderValue`/`ResolveRefs`/`FieldLabel`/`FIELD_LABELS` 定义，改为 `import ReportView from './ReportView'`（这些定义体已搬入 Task 3 的 `ReportView.jsx`，此处只删）。
2. `import AuditWorkbench from './AuditWorkbench'`、`import RawJson from './RawJson'`。
3. 任务列表增加状态筛选：新增 local state `const [statusFilter, setStatusFilter] = useState('all')`，渲染前用 `tasks.filter((t) => statusFilter === 'all' || t.status === statusFilter)`；在"历史任务"卡片头部加状态筛选按钮组（全部/进行中/成功/失败）。
4. 任务详情中 [`/timeline` 数据由 `AuditWorkbench` 自行获取]：`AuditWorkbench` 组件内部 `useEffect` 依赖 `taskId` 请求 `/api/tasks/{taskId}/timeline`，与现有 `<Timeline taskId={...} />` 的取数方式一致（各自独立 fetch，不改变 Timeline 现有行为）。在 `{selectedTask?.result?.agent && <AgentPlan .../>}` 之后追加：

   ```jsx
   {selectedTask && <AuditWorkbench taskId={selectedTask.task_id} />}
   <RawJson label="任务结果原始 JSON" json={selectedTask?.result} />
   ```

   为此，`AuditWorkbench` 组件需调整为接收 `taskId` prop 并在内部 fetch（与上面 Step 3 定义一致，仅在 `buildTree` 纯函数外增加取数逻辑）。实际上只需让 `AuditWorkbench` 在 `useEffect(() => { fetch('/api/tasks/${taskId}/timeline')... }, [taskId])` 后拿到 `timeline` 再 `buildTree`。

> 注意：Step 3 中的 `AuditWorkbench({ timeline })` 是纯展示组件；为与现有 `Timeline` 各自取数的模式一致，可在 `App.jsx` 中新增 `timeline` state 并通过一个 `useEffect` 请求 `/api/tasks/{id}/timeline` 后，将结果同时传给 `<Timeline>` 与 `<AuditWorkbench>`。**两种接法二选一**：A) 在 App 层建 `timeline` state 统一获取并传给两个组件；B) 让 `AuditWorkbench` 内部自取。为最小改动、不碰 `Timeline`，推荐 **B**：保留 `<Timeline taskId={...} />` 自取，`AuditWorkbench` 同样自取（接收 `taskId` prop）。

- [ ] **Step 5: 运行确认通过**

Run: `cd page && npx vitest run tests/auditWorkbench.test.jsx`
Expected: PASS。
再验证打包与 lint：
```bash
cd page && npm run build && npm run lint
```
Expected: build 成功、lint 无 error。

- [ ] **Step 6: 提交**

```bash
git add page/src/AuditWorkbench.jsx page/src/AuditWorkbench.css page/src/RawJson.jsx page/src/ReportView.jsx page/src/EvidencePanel.jsx page/src/App.jsx page/src/App.css page/tests/auditWorkbench.test.jsx page/vitest.config.js page/package.json page/package-lock.json
git commit -m "feat(page): V0.5 审计工作台（主子任务树/工具摘要/证据反查/原始JSON）"
```

---

### Task 4: V0.6 验收基建文档

**Files:**
- Create: `docs/specs/2026-09-09-v06-scoring-rubric.md`
- Create: `docs/specs/2026-09-09-v06-regression-set.md`
- Create: `docs/specs/2026-09-09-v06-acceptance.md`
- Modify: `docs/how-it-works.md`（前端章节更新为审计工作台描述）

**Interfaces:**
- Consumes: V0.6 设计文档 §6；《V0开发计划》4.7 节建议评分维度。
- Produces: 三份验收模板 + how-it-works 前端描述同步。

- [ ] **Step 1: 评分表**

`docs/specs/2026-09-09-v06-scoring-rubric.md`：

```markdown
# V0.6 三模式人工评分表（模板）

> 用途：业务专家对「一次性 LLM 报告 / 固定流程 / 受控 Agent」三模式在同一批
> 真实任务上的对照打分。1—5 分（5 最优），每维打分后附一句理由。
>
> 说明：真实评分与结论在测试环境正式对照时由业务专家现场填写，本模板不含评分结果。

## 评分维度（源自《V0开发计划》4.7 建议维度）

| 维度 | 说明 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| 重要问题发现完整性 | 是否发现关键升温问题/风险/机会 | ... | ... | ... | ... | ... |
| 证据准确性与覆盖率 | 关键判断是否绑定有效证据，覆盖是否充分 | ... | ... | ... | ... | ... |
| 反证与不确定性处理 | 是否呈现反例、样本偏差与不确定性 | ... | ... | ... | ... | ... |
| 风险/机会判断合理性 | 判断是否合理、有依据 | ... | ... | ... | ... | ... |
| 策略建议可执行性 | 建议是否具体、可落地 | ... | ... | ... | ... | ... |
| 多次运行一致性 | 同一任务重复运行结论是否稳定 | ... | ... | ... | ... | ... |
| Token/耗时/工具成本 | 单次运行资源消耗 | ... | ... | ... | ... | ... |

## 使用方式

1. 每个任务对三模式分别评分；
2. 记录任务输入、模式、得分、关键理由；
3. 汇总各维度平均分，横向对比三模式；
4. 供 V0.6 评审回答 GO/ADJUST/STOP。
```

- [ ] **Step 2: 回归任务集**

`docs/specs/2026-09-09-v06-regression-set.md`：列出 5—10 个真实历史舆情任务（含至少一个异常升温案例、一个数据不足案例），每个任务固化输入、预期证据点与验收通道。模板：

```markdown
# V0.6 最小回归任务集（模板）

> 用途：选定 5—10 个真实历史任务，作为三模式对照与最终验收的统一输入。
> 真实任务输入、预期证据点由现场从真实舆情数据中选定后回填；本文件先给结构。

## 任务清单

| 序号 | 分析对象 | 时间窗口 | 案例类型 | 预期证据点 | 验收通道 |
|---|---|---|---|---|---|
| 1 | （回填） | 近30天 | 异常升温 | 高温视频/评论ID | （回填） |
| 2 | （回填） | 近30天 | 数据不足 | 覆盖率低 | ... |
| ... | ... | ... | ... | ... | ... |
| 5—10 | ... | ... | ... | ... | ... |

## 跑取命令

```bash
# 三模式对照：对每个任务分别用 mode 创建
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"oneshot"}' -H 'Content-Type: application/json'
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"baseline"}' -H 'Content-Type: application/json'
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"agent"}' -H 'Content-Type: application/json'
```

> 注：`mode="baseline"` 对应 V0.2 固定流程（当前 `use_workflow=False && use_agent=False` 的默认路径）；`mode="oneshot"` 为 V0.6 新增。
```

- [ ] **Step 3: 验收记录模板**

`docs/specs/2026-09-09-v06-acceptance.md`：

```markdown
# V0.6 正式验收记录（模板）

> 在测试环境完成三模式对照与人工评分后填写。最终结论由业务评审给出，本文件为记录模板。

## 一、系统状态

- 部署环境：测试服务器 / Docker Compose
- LLM 接入：真实 API（模型）
- MySQL：只读接入
- 核心链路：无模拟实现（确认）

## 二、对照结果摘要

| 任务 | 一次性LLM | 固定流程 | 受控Agent | 备注 |
|---|---|---|---|---|
| 任务1 | （得分） | | | |
| ... | | | | |

## 三、四门问题回答（源自《V0开发计划》§9）

1. 受控 Agent 是否比固定流程发现更多重要问题 / 更可靠判断？
2. 增益是否覆盖额外 Token/耗时/不稳定性/成本？
3. 最应扩展：数据工具 / Skill / Agent 自主范围 / 业务场景？
4. 哪些接口稳定、哪些需 V1 前重构？

## 四、结论

- [ ] GO
- [ ] ADJUST
- [ ] STOP

## 五、已知问题与 V1 建议
```

- [ ] **Step 4: how-it-works 前端描述同步**

在 `docs/how-it-works.md` 相关小节补充：V0.5 审计工作台视图（任务筛选、运行审计主子任务树、工具输入/返回摘要、结论↔证据双向反查、策略包报告、原始 JSON 查看）；V0.6 oneshot 模式与三模式对照。保持简洁，不重复实现细节。

- [ ] **Step 5: 运行确认**

无需跑代码。检查文档存在且段落完整：
```bash
ls docs/specs/2026-09-09-v06-*.md docs/specs/2026-09-09-v06-acceptance.md
grep -n "审计工作台\|oneshot\|三模式" docs/how-it-works.md
```

- [ ] **Step 6: 提交**

```bash
git add docs/specs/2026-09-09-v06-scoring-rubric.md docs/specs/2026-09-09-v06-regression-set.md docs/specs/2026-09-09-v06-acceptance.md docs/how-it-works.md
git commit -m "docs(v06): 三模式对照评分表/回归任务集/验收记录模板 + how-it-works 前端章节"
```

---

## 自审记录（written-plans 检查）

- **Spec 覆盖**：§1.3 交付边界 → Task 2（oneshot）+ Task 3（前端）+ Task 4（文档）；§4.1 事件摘要 → Task 1；§5 视图 → Task 3；§6 验收基建 → Task 2/4。双向反查（§5.2）在 Task 3 用 `buildRefMap`/`referenced_by` 实现。
- **占位符**：无 TBD/TODO；第 4 步骤中"按 App.jsx 原文搬移"是明确的、可执行的指引（被搬移代码已在 App.jsx 中完整存在）。
- **类型一致性**：`buildTree`/`AuditWorkbench`/`ToolDetail`/`buildRefMap` 在 Task 3 前后定义一致；`result_summary`/`bias_note` 字段名与 Task 1 后端产出一致。
- **依赖安装**：前端测试新装 vitest/jsdom/testing-library 已在 Task 3 Step 1 说明；不影响现有 Vite 构建。

---


