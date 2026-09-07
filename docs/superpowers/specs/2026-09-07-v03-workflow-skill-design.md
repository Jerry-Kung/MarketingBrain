# Marketing Brain V0.3 设计文档：受控工作流与 Skill 机制

**日期**：2026-09-07  
**版本**：V0.3  
**状态**：设计已完成，待评审  

---

## 1. 概述

### 1.1 目标

V0.3 将 V0.2 的固定分析流水线升级为**可审计的分阶段工作流**，引入 **Skill 机制**作为舆情分析 SOP 的载体。核心目标：

1. **Skill 机制验证**：用 YAML 声明式配置定义 Skill（stages + 每阶段工具白名单 + prompts + 输出 schema）
2. **受控工作流**：工作流引擎按 Skill 定义的阶段顺序执行，工具授权在运行时拦截
3. **细粒度审计**：记录每个 stage 开始/完成、每次工具调用、未授权调用被拒绝等事件
4. **证据引用图**：扩展 `EvidenceStore`，支持 judgment / assumption，记录双向引用关系
5. **前端时间线**：展示 stage 与工具调用过程，停止原因可视化

### 1.2 非目标（V0.3 不做）

- Agent 自主规划与多步下钻（保留到 V0.4）
- 任务崩溃后的自动恢复（V0.3 只保证阶段顺序执行，崩溃需重启任务）
- 复杂 Skill 表达能力（如条件分支、循环）
- Skill 热更新或版本共存

---

## 2. 架构设计

### 2.1 模块结构

```
app/
  skill/
    __init__.py
    loader.py          # 加载 & 验证 Skill YAML，版本校验，缓存
    schema.py          # SkillDefinition / StageDefinition dataclass
  workflow/
    __init__.py
    engine.py          # StateMachine：stage loop, LLM dispatch, tool enforcement
    context.py         # 构建 stage 专用 LLM 上下文（system prompt + 已有证据）
  analysis/
    tools.py           # (现有) 8 个确定性统计工具
    registry.py        # NEW: tool name → callable mapping
  (现有模块不变: api, understanding, snapshot, datasource, store, llm, pipeline)

skills/
  opinion-pulse.yaml
  evidence-review.yaml
  strategy-synthesis.yaml
```

### 2.2 核心接口

**Skill YAML 结构**（`skills/opinion-pulse.yaml` 示例）：

```yaml
version: "0.3.0"
name: "opinion-pulse"
description: "常规舆情脉搏分析"

stages:
  - name: "snapshot"
    system_prompt: "你是数据边界确认专员。根据用户意图，确认本次分析的数据范围。"
    tools: []
    output_schema:
      type: "object"
      required: ["scope_confirmed"]
      properties:
        scope_confirmed: {type: "boolean"}
        
  - name: "investigate"
    system_prompt: "你是舆情调查分析师。使用工具获取数据并识别关键主题。"
    tools:
      - data_coverage
      - volume_trend
      - period_comparison
      - topic_frequency_tool
      - sample_comments
    output_schema:
      type: "object"
      required: ["findings"]
      
  - name: "synthesize"
    system_prompt: "你是策略综合专家。基于调查结果生成舆情策略包。"
    tools:
      - drill_evidence
    output_schema:
      type: "object"
      required: ["report"]
```

**SkillDefinition dataclass** (`app/skill/schema.py`):

```python
@dataclass
class StageDefinition:
    name: str
    system_prompt: str
    tools: list[str]           # 本阶段授权的工具白名单
    output_schema: dict        # JSON schema 校验 LLM 输出

@dataclass
class SkillDefinition:
    version: str
    name: str
    description: str
    stages: list[StageDefinition]
    
    @classmethod
    def from_yaml(cls, path: Path) -> "SkillDefinition":
        ...
```

**Workflow engine 接口** (`app/workflow/engine.py`):

```python
class WorkflowEngine:
    def __init__(self, skill: SkillDefinition, datasource, snapshot, 
                 evidence_store, llm_provider, task_repo, event_repo):
        ...
        
    def run(self, task_id: str) -> dict:
        """执行完整 Skill 定义的 stages，返回最终 result。"""
        for stage in self.skill.stages:
            self._execute_stage(task_id, stage)
        return self._build_result()
```

**Tool registry** (`app/analysis/registry.py`):

```python
TOOL_REGISTRY: dict[str, Callable] = {
    "data_coverage": tools.data_coverage,
    "volume_trend": tools.volume_trend,
    # ... 全部 8 个工具
}

def get_tool(name: str) -> Callable:
    if name not in TOOL_REGISTRY:
        raise ToolNotFoundError(f"Tool {name} not registered")
    return TOOL_REGISTRY[name]
```

---

## 3. Skill 加载与工具授权流程

### 3.1 Skill 加载流程

**启动时加载** (`app/skill/loader.py`)

```python
class SkillLoader:
    def __init__(self, skills_dir: Path = Path("skills")):
        self.skills_dir = skills_dir
        self._cache: dict[str, SkillDefinition] = {}
    
    def load(self, skill_name: str) -> SkillDefinition:
        """加载并验证 Skill YAML，缓存结果。"""
        if skill_name in self._cache:
            return self._cache[skill_name]
        
        skill_path = self.skills_dir / f"{skill_name}.yaml"
        if not skill_path.exists():
            raise SkillNotFoundError(f"Skill {skill_name} not found at {skill_path}")
        
        with open(skill_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        # 版本校验：V0.3 只接受 "0.3.0"
        if data.get("version") != "0.3.0":
            raise SkillVersionError(f"Skill {skill_name} version mismatch")
        
        skill = SkillDefinition.from_dict(data)
        self._validate_schema(skill)
        self._cache[skill_name] = skill
        return skill
```

**API 层选择 Skill** (`app/api/routes.py` 修改)

```python
@app.post("/api/tasks", response_model=TaskResponse)
def create_task(req: CreateTaskRequest):
    intent = parser.parse(req.raw_input)
    snap = LogicalSnapshot.from_intent(intent)
    
    # 根据意图的 goal_type 选择 Skill（V0.3 简化映射）
    skill_name = _select_skill(intent.goal_type)  # "opinion-pulse"
    
    task = task_repo.create_task(
        raw_input=req.raw_input,
        parsed_intent=intent.to_dict(),
        snapshot=snap.to_dict(),
        skill_name=skill_name,  # NEW 字段
    )
    
    # 后台执行：用 WorkflowEngine 替代 BaselinePipeline
    if background:
        start_workflow_background(task.task_id, skill_name, ...)
```

### 3.2 工具授权流程

**Stage 执行时的工具调用拦截** (`app/workflow/engine.py`)

```python
class WorkflowEngine:
    def _execute_stage(self, task_id: str, stage: StageDefinition):
        self.event_repo.append_event(task_id, "stage_start", {"stage": stage.name})
        
        messages = self._build_stage_context(stage)
        response = self.llm_provider.chat(messages, tools=self._get_available_tools())
        
        # 检查 LLM 返回的 tool_calls
        if response.tool_calls:
            for call in response.tool_calls:
                tool_name = call["name"]
                
                # 授权检查：tool_name 必须在 stage.tools 白名单中
                if tool_name not in stage.tools:
                    error_msg = (
                        f"Stage {stage.name} attempted unauthorized tool call: {tool_name}. "
                        f"Allowed tools: {', '.join(stage.tools)}"
                    )
                    self.event_repo.append_event(task_id, "tool_unauthorized", {
                        "stage": stage.name, "tool": tool_name, "allowed": stage.tools
                    })
                    raise ToolAuthorizationError(error_msg)
                
                # 执行授权工具
                tool_fn = get_tool(tool_name)
                result = tool_fn(self.datasource, self.snapshot, self.evidence_store)
                
                self.event_repo.append_event(task_id, "tool_call", {
                    "stage": stage.name,
                    "tool": tool_name,
                    "params": call.get("arguments", {}),
                    "sample_size": result.get("sample_size", 0),
                })
        
        self._validate_stage_output(stage, response.content)
        self.event_repo.append_event(task_id, "stage_done", {"stage": stage.name})
```

**关键决策**：

1. **工具白名单校验时机**：LLM 返回 `tool_calls` 后、执行工具前
2. **未授权调用处理**：捕获、记录 `tool_unauthorized` 事件、任务失败并保留清晰错误信息
3. **版本校验**：loader 只接受 `version: "0.3.0"`

---

## 4. 事件审计与证据引用图

### 4.1 审计事件类型

V0.3 新增事件类型（`app/store/repository.py` 的 `EventRepository`）：

```python
# V0.2 已有
"task_created"
"task_started"
"task_finished"
"task_failed"

# V0.3 新增
"skill_selected"       # 选择了哪个 Skill
"stage_start"          # 阶段开始
"stage_done"           # 阶段完成
"tool_call"            # 工具调用（含工具名、参数摘要、样本量）
"tool_unauthorized"    # 未授权工具调用被拒绝
"evidence_registered"  # 证据登记
"judgment_made"        # 结构化判断
"assumption_added"     # 假设新增
```

**事件 payload 示例**：

```python
# tool_call
{
    "stage": "investigate",
    "tool": "sample_comments",
    "params_summary": {"limit": 30, "keyword": None},
    "sample_size": 28,
    "evidence_ids": ["abc123", "def456", ...]
}

# tool_unauthorized
{
    "stage": "investigate",
    "tool": "drill_evidence",
    "allowed_tools": ["data_coverage", "volume_trend", "sample_comments"]
}
```

### 4.2 证据引用图

`app/store/evidence.py` 扩展：

```python
@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str  # comment | video | stat | judgment | assumption
    # ... 原有字段 ...
    referenced_by: list[str] = field(default_factory=list)  # 被哪些 judgment 引用
    
class EvidenceStore:
    def register_judgment(self, *, judgment_type: str, title: str, 
                          evidence_refs: list[str], source: str) -> EvidenceRecord:
        """登记结构化判断（风险/机会/主题），记录引用的证据 ID。"""
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="judgment",
            source=source,
            extra={"judgment_type": judgment_type, "title": title, "evidence_refs": evidence_refs}
        )
        # 反向引用
        for ref_id in evidence_refs:
            if ref_id in self._comments:
                self._comments[ref_id].referenced_by.append(ev.evidence_id)
        return ev
```

**引用关系的作用**：

1. 前端可视化：点击判断 → 展示支撑证据
2. 反向追溯：点击评论 → 显示"该评论被 N 个判断引用"
3. 审计完整性：验证每个关键判断是否关联至少一项证据

---

## 5. 前端时间线视图

### 5.1 新增 API 端点

**`GET /api/tasks/{task_id}/timeline`** (`app/api/routes.py`)

```python
@app.get("/api/tasks/{task_id}/timeline", response_model=TimelineResponse)
def get_timeline(task_id: str):
    task = task_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    
    events = event_repo.get_events(task_id)
    timeline = [
        {"event_type": ev.event_type, "payload": ev.payload, "seq": ev.seq, "created_at": ev.created_at}
        for ev in events
    ]
    return TimelineResponse(task_id=task_id, events=timeline)
```

### 5.2 Timeline 视图组件

**`page/src/Timeline.jsx`**

```jsx
function Timeline({ events }) {
  const stages = []
  let currentStage = null
  
  for (const ev of events) {
    if (ev.event_type === 'stage_start') {
      currentStage = { name: ev.payload.stage, tools: [], status: 'running' }
      stages.push(currentStage)
    } else if (ev.event_type === 'tool_call' && currentStage) {
      currentStage.tools.push({ name: ev.payload.tool, sample_size: ev.payload.sample_size })
    } else if (ev.event_type === 'stage_done' && currentStage) {
      currentStage.status = 'done'
    } else if (ev.event_type === 'tool_unauthorized' && currentStage) {
      currentStage.status = 'failed'
      currentStage.error = `未授权工具: ${ev.payload.tool}`
    }
  }
  
  return (
    <div className="timeline">
      {stages.map((stage, i) => (
        <div key={i} className={`stage stage-${stage.status}`}>
          <div className="stage-header">
            <span className="stage-name">{stage.name}</span>
            <span className={`stage-status status-${stage.status}`}>{stage.status}</span>
          </div>
          {stage.tools.length > 0 && (
            <ul className="tool-list">
              {stage.tools.map((tool, j) => (
                <li key={j}>{tool.name} ({tool.sample_size} 样本)</li>
              ))}
            </ul>
          )}
          {stage.error && <p className="error">{stage.error}</p>}
        </div>
      ))}
    </div>
  )
}
```

**交互设计**：

1. 时间线默认展开，与报告并排或上下排列
2. 任务 running 时每 2.5s 轮询刷新
3. 停止原因（如未授权工具）在对应 stage 标红显示

---

## 6. 错误处理

### 6.1 失败场景分类

1. **Skill 加载失败**：YAML 不存在、版本不匹配、schema 非法
2. **Stage 执行失败**：LLM 超时、返回非 JSON、输出不符合 output_schema
3. **工具授权失败**：LLM 尝试调用未授权工具
4. **工具执行失败**：datasource 查询异常
5. **证据引用失败**：报告引用不存在的 evidence_id（记录但不阻止完成）

### 6.2 错误处理策略

**Skill 加载失败**：任务创建时直接失败，不启动后台线程，记录 `task_failed` 事件。

**Stage 执行失败**：

```python
try:
    response = self.llm_provider.chat(messages, timeout=stage_timeout)
    self._validate_stage_output(stage, response.content)
except LLMError as e:
    self.event_repo.append_event(task_id, "stage_failed", {
        "stage": stage.name, "error": str(e), "error_type": "llm_error"
    })
    raise WorkflowExecutionError(f"Stage {stage.name} LLM 调用失败: {e}")
except ValidationError as e:
    self.event_repo.append_event(task_id, "stage_failed", {
        "stage": stage.name, "error": str(e), "error_type": "schema_validation"
    })
    raise WorkflowExecutionError(f"Stage {stage.name} 输出格式非法: {e}")
```

**工具授权失败**：记录 `tool_unauthorized` 事件，抛出 `ToolAuthorizationError`，任务失败。

**任务状态转换**：`pending → running → success` 或 `running → failed`

---

## 7. 数据库 schema 扩展与配置

### 7.1 TaskRepository schema 扩展

```sql
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    raw_input    TEXT NOT NULL DEFAULT '',
    parsed_intent TEXT,
    snapshot     TEXT,
    skill_name   TEXT,           -- NEW: 记录使用的 Skill
    result       TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
```

**向下兼容**：V0.2 已有任务的 `skill_name` 为 `NULL`，前端可标注"基线版本（无 Skill）"。

### 7.2 配置扩展

**`app/core/config.py` 新增**：

```python
SKILLS_DIR: str = Field(default="skills")
DEFAULT_SKILL: str = Field(default="opinion-pulse")
WORKFLOW_STAGE_TIMEOUT_MS: int = Field(default=300000)
ENABLE_WORKFLOW_ENGINE: bool = Field(default=True)  # 启用 V0.3 工作流

@property
def workflow_stage_timeout(self) -> float:
    return self.WORKFLOW_STAGE_TIMEOUT_MS / 1000.0
```

**`.env.example` 追加**：

```ini
# V0.3 Skill 与工作流配置
SKILLS_DIR=skills
DEFAULT_SKILL=opinion-pulse
WORKFLOW_STAGE_TIMEOUT_MS=300000
ENABLE_WORKFLOW_ENGINE=true
```

### 7.3 依赖新增

```
pyyaml>=6.0
jsonschema>=4.20.0
```

---

## 8. 测试策略

### 8.1 测试分层

**单元测试**：
- `tests/test_skill_loader.py`：Skill 加载、版本校验、schema 验证、缓存
- `tests/test_skill_schema.py`：序列化 / 反序列化
- `tests/test_tool_registry.py`：工具注册表、未注册工具抛错
- `tests/test_workflow_engine.py`：阶段循环、工具授权检查、输出 schema 校验（用 mock LLM）
- `tests/test_workflow_context.py`：Stage 上下文构建

**集成测试**：
- `tests/test_workflow_integration.py`：真实 datasource + mock LLM，完整 `opinion-pulse` 工作流
- `tests/test_api_v03.py`：API 层创建任务 → 选择 Skill → 查询 timeline

**关键测试场景**：
- Skill 加载失败（文件不存在、版本不匹配）
- 工具授权拦截（LLM 尝试未授权工具）
- Stage 输出 schema 校验失败
- 证据引用图完整性验证

**真实 LLM 冒烟测试**：端到端任务（如"分析坦克300近期舆情"），用真实 LLM + MySQL，验证：
- Skill 正确加载
- 各 stage 按顺序执行
- 工具调用成功并记录事件
- 报告生成且证据引用有效

---

## 9. 迁移策略与向下兼容

### 9.1 V0.2 → V0.3 迁移路径

V0.2 基线流水线保留，两条路径并存：

**API 层路由逻辑** (`app/api/routes.py`)

```python
@app.post("/api/tasks", response_model=TaskResponse)
def create_task(req: CreateTaskRequest):
    intent = parser.parse(req.raw_input)
    snap = LogicalSnapshot.from_intent(intent)
    
    use_workflow = settings.ENABLE_WORKFLOW_ENGINE  # 新增配置项
    if req.force_baseline:  # 请求参数可强制使用 V0.2
        use_workflow = False
    
    task = task_repo.create_task(
        raw_input=req.raw_input,
        parsed_intent=intent.to_dict(),
        snapshot=snap.to_dict(),
        skill_name=_select_skill(intent.goal_type) if use_workflow else None,
    )
    
    if use_workflow:
        from app.workflow.runner import start_workflow_background
        start_workflow_background(task.task_id, ...)
    else:
        from app.pipeline.runner import start_task_background
        start_task_background(task_id=task.task_id, ...)
```

**向下兼容保证**：
- `app/pipeline/baseline.py` 和 `app/pipeline/runner.py` 保持不变
- 已有 V0.2 任务（`skill_name` 为 `NULL`）历史数据不受影响
- `ENABLE_WORKFLOW_ENGINE` 默认 `True`，可全局切换回 V0.2 模式

**前端兼容展示**：

```jsx
function TaskItem({ task }) {
  const mode = task.skill_name ? `Skill: ${task.skill_name}` : "基线模式 (V0.2)"
  return (
    <li className="task-item">
      <span className="task-mode">{mode}</span>
      {/* ... */}
    </li>
  )
}

// V0.2 任务不展示 timeline
{task.skill_name && <Timeline events={timeline?.events} />}
```

### 9.2 回滚预案

设置 `ENABLE_WORKFLOW_ENGINE=false` + 重启应用，所有新任务自动使用 V0.2 基线。已创建的 V0.3 任务保留在数据库可事后查看。

---

## 10. 实施计划

按 superpowers:writing-plans 流程，将本设计拆解为可独立测试的任务：

1. Skill schema 与 loader（含测试）
2. 工具注册表（含测试）
3. 工作流引擎核心（stage loop + 工具授权拦截，含测试）
4. 工作流上下文构建（含测试）
5. 证据引用图扩展（judgment / assumption，含测试）
6. API 层集成（选择 Skill + 后台启动工作流）
7. 数据库 schema 扩展与配置
8. 前端 timeline 视图（API + 组件）
9. 三个 Skill YAML 文件编写
10. 集成测试与真实 LLM 冒烟
11. 文档更新（architecture.md / how-it-works.md / core-design.md）

---

## 附录：设计决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| Skill 文件格式 | YAML 声明式配置 | V0.3 目标是验证机制，三个固定 Skill 不需 Python 逻辑；declarative 保持 Skill 可审计、engine 简单 |
| 工具白名单粒度 | 每 stage 独立白名单 | 不同阶段需要不同工具（如 synthesize 可补充证据），stage 级白名单更灵活 |
| 授权校验时机 | LLM 返回后、执行前 | LLM 可"尝试"任何工具，engine 捕获未授权调用作为审计事件，任务失败但有清晰错误 |
| 事件粒度 | 一次工具调用一个事件 | 细粒度审计，便于前端时间线展示和后续分析 |
| 崩溃恢复 | V0.3 不支持，V0.4+ | V0.3 聚焦机制验证，崩溃恢复需额外状态持久化与幂等性设计，延后到 V0.4 |
| V0.2 兼容 | 保留基线流水线，配置切换 | 需对照验证 V0.3 增益，保留 V0.2 作为 fallback |

---

**设计完成，待用户评审与批准后进入 writing-plans 阶段。**
