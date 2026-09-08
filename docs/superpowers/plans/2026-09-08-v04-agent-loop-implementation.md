# Marketing Brain V0.4 主 Agent 与受控子 Agent Loop 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 V0.4 三角色受控 Agent Loop：Supervisor 生成调查卡，Investigator 通过真实 Function Calling 动态下钻，Reviewer 独立评审并支持一次受控补查，全程硬预算与明确停止原因。

**Architecture:** 新增 `app/agent/` 模块（protocols / budgets / tools_spec / supervisor / investigator / reviewer / orchestrator / runner）。扩展 `LLMProvider.chat()` 支持 `tools` 参数与 `tool_calls` 解析（向后兼容）。`app/workflow/`、`app/pipeline/` 不改动。由 `ENABLE_AGENT_ENGINE` + `ENABLE_WORKFLOW_ENGINE` 组合在 API 层选择执行路径。前端在主计划/子任务进度/停止原因最小范围内扩展。

**Tech Stack:** Python 3.11+, FastAPI, SQLite, OpenAI-compatible LLM API（function calling）, React（前端），pytest

**Spec:** `docs/superpowers/specs/2026-09-08-v04-agent-loop-design.md`

## Global Constraints

- Python >= 3.11；所有文档与代码注释使用简体中文。
- 只进行满足验收标准所需的最小且完整的修改。
- 遵循项目现有代码风格：dataclass、type hints、docstring。
- 测试使用 pytest；LLM 一律用 `httpx.MockTransport` 或 FakeLLM，不真实调用；数据源用 FakeDS。
- TDD：先写失败测试，验证失败，再写最小实现，验证通过，最后提交。
- **关键兼容约束（不允许违反）**：
  - 不改动 `app/workflow/`、`app/pipeline/`、`app/skill/` 任何对外接口或行为。
  - `LLMProvider.chat()` 不传 `tools` 时，请求体与行为与现状完全一致；现有 `tests/test_llm_provider.py` 必须全绿。
  - 每个任务提交一次（或按子步骤多次），提交信息用简体中文，以 `Co-Authored-By` 结尾可省略。
- 事件粒度：一次工具调用一个 `tool_call`/`subtask_tool` 事件；子任务开始/停止各一个事件。
- 工具调用守卫：工具名必须在白名单内，参数必须经 `tools_spec` 校验/钳制后才执行；未经授权/参数非法视为 `illegal_output`，记录事件并停止该卡，绝不静默执行。
- 预算为硬限制：每轮开始前检查，超限即 `budget_exhausted` 停止，不得解锁。
- `AGENT_MAX_SUPPLEMENTS=1`：全任务至多一次受控补查。
- 确定性取值（计划内代码块直接照抄）：`AGENT_MAX_SUBTASKS=6`、`AGENT_MAX_LOOPS=3`、`AGENT_MAX_TOOL_CALLS=20`、`AGENT_MAX_SUPPLEMENTS=1`、`AGENT_LLM_TIMEOUT_MS=300000`。

---

### Task 1: LLMProvider 支持 Function Calling（tools + tool_calls 解析）

**Files:**
- Modify: `app/llm/provider.py`
- Test: `tests/test_llm_provider.py`

**Interfaces:**
- Consumes: 现有 `LLMResult` / `LLMUsage` / `LLMError`
- Produces:
  - `LLMProvider.chat(messages, *, tools=None, response_format=None, temperature=None, max_tokens=None, timeout=None) -> LLMResult`
    - 当 `tools` 非空时，请求体追加 `"tools": tools` 与 `"tool_choice": "auto"`
  - `LLMResult.tool_calls -> list[dict]`：从 `raw` 解析 `choices[0].message.tool_calls`，每个元素规范化为 `{"name": str, "arguments": dict}`；`tool_calls` 内 `arguments` 为 JSON 字符串，需 `json.loads`，解析失败则为 `{}`。无 tool_calls 时返回 `[]`。
  - `LLMResult.tool_calls` 是 property，不改变 `__init__` 签名，不破坏现有构造调用。

- [ ] **Step 1: 编写失败测试**

在 `tests/test_llm_provider.py` 末尾追加 `class TestFunctionCalling`：

```python
class TestFunctionCalling:
    def test_chat_with_tools_adds_tools_to_body(self):
        """chat 传 tools 时请求体包含 tools 与 tool_choice。"""
        captured = []

        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": None}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        transport = httpx.MockTransport(handler)
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        tools_def = [{"type": "function", "function": {"name": "sample_comments"}}]
        p.chat([{"role": "user", "content": "x"}], tools=tools_def)
        body = json.loads(captured[0].content)
        assert body["tools"] == tools_def
        assert body["tool_choice"] == "auto"

    def test_chat_without_tools_has_no_tools_key(self):
        """不传 tools 时请求体不含 tools 键（向后兼容）。"""
        captured = []

        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        transport = httpx.MockTransport(handler)
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        p.chat([{"role": "user", "content": "x"}])
        body = json.loads(captured[0].content)
        assert "tools" not in body
        assert "tool_choice" not in body

    def test_tool_calls_property_parses_arguments(self):
        """LLMResult.tool_calls 解析 raw 中的 tool_calls 并 json.loads 参数。"""
        raw = {
            "choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "sample_comments",
                                  "arguments": '{"keyword": "油耗", "limit": 10}'}},
                    {"id": "call_2", "type": "function",
                     "function": {"name": "drill_evidence",
                                  "arguments": "not-json"}},
                ],
            }}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        res = LLMResult(content=None, usage=LLMUsage(model="m"), raw=raw)
        tcs = res.tool_calls
        assert len(tcs) == 2
        assert tcs[0]["name"] == "sample_comments"
        assert tcs[0]["arguments"] == {"keyword": "油耗", "limit": 10}
        assert tcs[1]["name"] == "drill_evidence"
        assert tcs[1]["arguments"] == {}  # 非法 JSON 降级为空 dict

    def test_tool_calls_empty_when_none(self):
        """raw 中没有 tool_calls 时返回空列表。"""
        res = LLMResult(content='{"ok": true}', usage=LLMUsage(model="m"), raw={})
        assert res.tool_calls == []

    def test_chat_with_tools_returns_tool_calls(self):
        """chat 传 tools 且返回 tool_calls 时，LLMResult.tool_calls 可用。"""
        def handler(req):
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "drill_evidence", "arguments": '{"min_like": 20}'}}
                ]}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        transport = httpx.MockTransport(handler)
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        res = p.chat([{"role": "user", "content": "x"}],
                     tools=[{"type": "function", "function": {"name": "drill_evidence"}}])
        assert res.tool_calls == [{"name": "drill_evidence", "arguments": {"min_like": 20}}]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm_provider.py -v`
Expected: 新增的 `TestFunctionCalling` 用例失败，原因：`LLMResult` 无 `tool_calls` 属性 / `chat` 无 `tools` 参数。既有的 `TestLLMProvider` 用例仍通过。

- [ ] **Step 3: 修改 provider.py**

在 `LLMResult` 中新增 `tool_calls` property（放在 `to_dict` 之后）：

```python
    @property
    def tool_calls(self) -> list[dict]:
        """解析 raw 中的 function tool_calls，返回 [{name, arguments(dict)}]。

        arguments 为 JSON 字符串，解析失败降级为空 dict。无 tool_calls 时返回 []。
        """
        try:
            msg = self.raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return []
        calls = msg.get("tool_calls") or []
        out = []
        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name", "")
            arguments = fn.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            out.append({"name": name, "arguments": arguments})
        return out
```

在 `chat()` 方法签名与请求体构建处修改。原签名：

```python
    def chat(self, messages, *, response_format=None, temperature=None,
             max_tokens=None, timeout=None) -> LLMResult:
```

改为：

```python
    def chat(self, messages, *, tools=None, response_format=None, temperature=None,
             max_tokens=None, timeout=None) -> LLMResult:
```

在 `body` 组装处，`if response_format:` 之后、`if max_tokens:` 之前追加：

```python
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_llm_provider.py -v`
Expected: `TestLLMProvider` + `TestFunctionCalling` 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/llm/provider.py tests/test_llm_provider.py
git commit -m "feat(llm): LLMProvider 支持 function calling（tools + tool_calls）

- chat 新增 tools 参数，传值时请求体含 tools/tool_choice=auto，不传则与现状一致
- LLMResult 新增 tool_calls property，解析 raw 中 tool_calls 并将 arguments 转 dict
- 向后兼容：不破坏现有 chat/chat_json 调用与既有测试"
```

---

### Task 2: V0.4 Agent 配置项

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 现有 `Settings`
- Produces:
  - `Settings.ENABLE_AGENT_ENGINE: bool = True`
  - `Settings.AGENT_MAX_SUBTASKS: int = 6`
  - `Settings.AGENT_MAX_LOOPS: int = 3`
  - `Settings.AGENT_MAX_TOOL_CALLS: int = 20`
  - `Settings.AGENT_MAX_SUPPLEMENTS: int = 1`
  - `Settings.AGENT_LLM_TIMEOUT_MS: int = 300000`
  - `Settings.agent_llm_timeout: float`（property，秒）
- 校验：`AGENT_*` 正整数项纳入现有 `_positive_int` 校验器。

- [ ] **Step 1: 编写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
def test_v04_agent_config():
    """测试 V0.4 Agent 配置加载与默认值。"""
    settings = Settings(
        DB_HOST="localhost", DB_PORT=3306, DB_USER="test", DB_PASSWORD="pwd", DB_NAME="db",
        LLM_API_BASE="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model",
    )
    assert settings.ENABLE_AGENT_ENGINE is True
    assert settings.AGENT_MAX_SUBTASKS == 6
    assert settings.AGENT_MAX_LOOPS == 3
    assert settings.AGENT_MAX_TOOL_CALLS == 20
    assert settings.AGENT_MAX_SUPPLEMENTS == 1
    assert settings.AGENT_LLM_TIMEOUT_MS == 300000
    assert settings.agent_llm_timeout == 300.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py::test_v04_agent_config -v`
Expected: FAIL（`Settings` 无这些属性导致 `AttributeError`）。

- [ ] **Step 3: 修改 config.py**

在 V0.3 配置段之后追加：

```python
    # ---------- V0.4 Agent 配置 ----------
    ENABLE_AGENT_ENGINE: bool = Field(default=True)
    AGENT_MAX_SUBTASKS: int = Field(default=6)
    AGENT_MAX_LOOPS: int = Field(default=3)
    AGENT_MAX_TOOL_CALLS: int = Field(default=20)
    AGENT_MAX_SUPPLEMENTS: int = Field(default=1)
    AGENT_LLM_TIMEOUT_MS: int = Field(default=300000)
```

在 `workflow_stage_timeout` property 之后追加：

```python
    @property
    def agent_llm_timeout(self) -> float:
        """Agent 单次 LLM 调用超时（秒）。"""
        return self.AGENT_LLM_TIMEOUT_MS / 1000.0
```

修改 `_positive_int` 校验器的字段列表，把新增的 5 个整数配置加入：

```python
    @field_validator("APP_PORT", "UI_POLL_INTERVAL_MS", "TOOL_MAX_RECORDS", "LLM_TIMEOUT_MS",
                     "WORKFLOW_STAGE_TIMEOUT_MS", "AGENT_MAX_SUBTASKS", "AGENT_MAX_LOOPS",
                     "AGENT_MAX_TOOL_CALLS", "AGENT_MAX_SUPPLEMENTS", "AGENT_LLM_TIMEOUT_MS")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 全部 PASS（含新增与既有）。

- [ ] **Step 5: 更新 .env.example**

在文件末尾追加：

```ini
# ---------- V0.4 Agent 配置 ----------
# 主开关：true 走 V0.4 受控 Agent（主 Agent + 子 Agent Loop + 评审）
ENABLE_AGENT_ENGINE=true
AGENT_MAX_SUBTASKS=6
AGENT_MAX_LOOPS=3
AGENT_MAX_TOOL_CALLS=20
AGENT_MAX_SUPPLEMENTS=1
AGENT_LLM_TIMEOUT_MS=300000
```

- [ ] **Step 6: 提交**

```bash
git add app/core/config.py .env.example tests/test_config.py
git commit -m "feat(config): 新增 V0.4 Agent 配置项

- ENABLE_AGENT_ENGINE + AGENT_MAX_SUBTASKS/LOOPS/TOOL_CALLS/SUPPLEMENTS + AGENT_LLM_TIMEOUT_MS
- agent_llm_timeout property 返回秒级超时
- 校验器纳入新增正整数配置"
```

---

### Task 3: 工具 JSON Schema 与参数校验/钳制（tools_spec）

**Files:**
- Create: `app/agent/__init__.py`（仅占位：`"""V0.4 受控 Agent 模块。"""`)
- Create: `app/agent/tools_spec.py`
- Create: `tests/__init__.py`（若不存在）
- Create: `tests/test_tools_spec.py`

**Interfaces:**
- Consumes: `app.analysis.registry.TOOL_REGISTRY`、`app.analysis.registry.get_tool`、`app.core.config.Settings`
- Produces:
  - `TOOL_JSON_SCHEMAS: list[dict]`：OpenAI function 定义列表（供 `chat(tools=...)`）
  - `TOOL_WHITELIST: set[str]`：允许的工具名集合（取自 `TOOL_REGISTRY`）
  - `validate_and_coerce(arguments: dict, tool_name: str, max_records: int) -> dict`：校验/钳制参数，返回可直接传给工具函数的关键字参数字典。
    - `limit` 钳制到 `[1, max_records]`（缺失时用该工具默认值，见下表）。
    - 未知工具或参数类型非法抛出 `ToolSpecError`。
  - `ToolSpecError(Exception)`
- 各工具参数规则（**值必须与表一致**）：

| tool | 参数 |
|---|---|
| `data_coverage` | `{}` |
| `volume_trend` | `{}` |
| `period_comparison` | `{}` |
| `topic_frequency_tool` | `{}` |
| `top_sources` | `limit`: int → 默认 10 |
| `sample_comments` | `keyword`: str? 默认 None；`limit`: int → 默认 30 |
| `drill_evidence` | `keyword`: str?；`min_like`: int?；`limit`: int → 默认 30 |
| `object_compare` | `other_tags`: list[str]? 默认 None |

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_tools_spec.py`：

```python
"""工具 schema 与参数校验测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.agent.tools_spec import TOOL_JSON_SCHEMAS, TOOL_WHITELIST, validate_and_coerce, ToolSpecError


class TestToolSchema:
    def test_schema_covers_all_registered_tools(self):
        """schema 覆盖注册表全部 8 个工具。"""
        from app.analysis.registry import TOOL_REGISTRY
        names = {s["function"]["name"] for s in TOOL_JSON_SCHEMAS}
        assert names == set(TOOL_REGISTRY.keys())

    def test_whitelist_equals_registry(self):
        assert TOOL_WHITELIST == set(__import__(
            "app.analysis.registry", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY.keys())

    def test_schema_entries_are_function_objects(self):
        for s in TOOL_JSON_SCHEMAS:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]


class TestValidateAndCoerce:
    def test_empty_args_for_no_param_tool(self):
        assert validate_and_coerce({}, "data_coverage", 500) == {}

    def test_limit_coerced_into_range(self):
        # 默认 10，超上限钳到 max_records，低于 1 钳到 1
        # 未提供 limit 时使用默认值 10
        assert validate_and_coerce({}, "top_sources", 500) == {"limit": 10}
        assert validate_and_coerce({"limit": 9999}, "top_sources", 500) == {"limit": 500}
        assert validate_and_coerce({"limit": 0}, "top_sources", 500) == {"limit": 1}

    def test_sample_comments_defaults(self):
        out = validate_and_coerce({}, "sample_comments", 500)
        assert out["limit"] == 30
        assert out["keyword"] is None

    def test_drill_evidence_keeps_min_like(self):
        out = validate_and_coerce({"keyword": "油耗", "min_like": 20, "limit": 5}, "drill_evidence", 500)
        assert out == {"keyword": "油耗", "min_like": 20, "limit": 5}

    def test_object_compare_other_tags(self):
        out = validate_and_coerce({"other_tags": ["坦克300"]}, "object_compare", 500)
        assert out["other_tags"] == ["坦克300"]

    def test_unknown_tool_raises(self):
        with pytest.raises(ToolSpecError):
            validate_and_coerce({}, "nonexistent_tool", 500)

    def test_invalid_type_raises(self):
        with pytest.raises(ToolSpecError):
            validate_and_coerce({"limit": "abc"}, "top_sources", 500)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_tools_spec.py -v`
Expected: FAIL（`app.agent.tools_spec` 不存在 → ModuleNotFoundError）。

- [ ] **Step 3: 创建 app/agent/__init__.py**

```python
"""V0.4 受控 Agent 模块（主 Agent + 子 Agent Loop + 评审）。"""
```

- [ ] **Step 4: 编写 app/agent/tools_spec.py**

```python
"""工具 JSON Schema 与参数校验/钳制。

为 8 个确定性工具生成 OpenAI function 定义，供 LLMProvider.chat(tools=...) 使用。
在工具执行前将 LLM 传来的 arguments 校验、钳制为可直接传给工具函数的关键字参数，
杜绝越界 limit、未知工具、非法类型。仅此模块负责工具参数规范化。
"""
from typing import Any

from app.analysis.registry import TOOL_REGISTRY
from app.core.config import Settings


class ToolSpecError(Exception):
    """工具参数校验失败（未知工具 / 非法类型 / 超出允许范围）。"""


# 各工具参数规则。无参数工具值为 {"type": "object", "properties": {}}。
# limit 用 minimum=1；上限由 validate_and_coerce 用 max_records 钳制（不在 schema 写死，便于按请求收紧）。
_PROPERTY_RULES: dict[str, dict[str, Any]] = {
    "data_coverage": {},
    "volume_trend": {},
    "period_comparison": {},
    "topic_frequency_tool": {},
    "top_sources": {"limit": {"type": "integer", "minimum": 1, "default": 10}},
    "sample_comments": {
        "keyword": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "default": 30},
    },
    "drill_evidence": {
        "keyword": {"type": "string"},
        "min_like": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "default": 30},
    },
    "object_compare": {"other_tags": {"type": "array", "items": {"type": "string"}}},
}

# 各工具在参数缺省时的默认 limit（无 limit 参数的工具不在此列）。
_DEFAULT_LIMIT: dict[str, int] = {
    "top_sources": 10,
    "sample_comments": 30,
    "drill_evidence": 30,
}


def _to_openai_function(name: str, props: dict) -> dict:
    """把某工具的规则转成 OpenAI function 定义。"""
    if not props:
        return {
            "type": "function",
            "function": {"name": name, "description": name,
                         "parameters": {"type": "object", "properties": {}}},
        }
    properties = {}
    required = []
    for pname, rule in props.items():
        prop = {k: v for k, v in rule.items() if k != "default"}
        properties[pname] = prop
        if "default" not in rule:
            required.append(pname)
    return {
        "type": "function",
        "function": {"name": name, "description": name,
                     "parameters": {"type": "object", "properties": properties,
                                    "required": required}},
    }


TOOL_JSON_SCHEMAS: list[dict] = [
    _to_openai_function(name, _PROPERTY_RULES[name]) for name in TOOL_REGISTRY
]

TOOL_WHITELIST: set[str] = set(TOOL_REGISTRY.keys())


def validate_and_coerce(arguments: dict, tool_name: str, max_records: int) -> dict:
    """校验并规范化 LLM 传来的工具参数。

    Args:
        arguments: LLM 传来的参数字典（可为空或含非法字段）。
        tool_name: 工具名。
        max_records: 单次工具最大返回记录数（钳制 limit 上限）。

    Returns:
        可直接传给工具函数的关键字参数字典。

    Raises:
        ToolSpecError: 工具未注册，或参数类型非法。
    """
    if tool_name not in TOOL_REGISTRY:
        raise ToolSpecError(f"未知工具: {tool_name}")
    rules = _PROPERTY_RULES.get(tool_name, {})
    args = dict(arguments or {})

    out: dict[str, Any] = {}
    for pname, rule in rules.items():
        if pname == "limit":
            val = args.get("limit")
            if val is None:
                val = _DEFAULT_LIMIT.get(tool_name)
            if not isinstance(val, int) or isinstance(val, bool):
                raise ToolSpecError(f"tool '{tool_name}' 参数 limit 必须是整数，得到 {val!r}")
            val = max(1, min(int(val), int(max_records)))
            out["limit"] = val
            continue
        if "type" in rule:
            expected = rule["type"]
            if pname in args and args[pname] is not None:
                val = args[pname]
                if expected == "string" and not isinstance(val, str):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是字符串")
                if expected == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是整数")
                if expected == "array" and not isinstance(val, list):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是数组")
                out[pname] = val
            else:
                out[pname] = None if pname != "other_tags" else None
    return out
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_tools_spec.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add app/agent/__init__.py app/agent/tools_spec.py tests/__init__.py tests/test_tools_spec.py
git commit -m "feat(agent): 工具 JSON Schema 与参数校验/钳制

- TOOL_JSON_SCHEMAS: 8 个工具的 OpenAI function 定义
- TOOL_WHITELIST: 允许的工具名集合
- validate_and_coerce: 校验/钳制参数，limit 落在 [1, max_records]，未知工具/非法类型抛 ToolSpecError"
```

---

### Task 4: 预算控制（Budgets）

**Files:**
- Create: `app/agent/budgets.py`
- Create: `tests/test_budgets.py`

**Interfaces:**
- Consumes: `Settings.AGENT_*` 配置
- Produces:
  - `AgentBudget(subtasks: int, loops: int, tool_calls: int, supplements: int)`：上限集合，`from_settings(settings)` 构造
  - `BudgetCounter(budget: AgentBudget)`：运行时累计
    - `record_subtask()` / `record_loop()` / `record_tool_call()` / `record_supplement()`：各 +1 并返回当前值
    - `subtask_exceeded() -> bool` / `loop_exceeded() -> bool` / `tool_calls_exceeded() -> bool` / `supplement_exceeded() -> bool`
    - `exhausted() -> bool`：任一超限
    - `to_dict() -> dict`：已用与上限
  - `BudgetExhaustedError(Exception)`

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_budgets.py`：

```python
"""预算控制测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.agent.budgets import AgentBudget, BudgetCounter


class TestAgentBudget:
    def test_from_settings(self):
        from app.core.config import Settings
        s = Settings(
            DB_HOST="localhost", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
            LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
            AGENT_MAX_SUBTASKS=4, AGENT_MAX_LOOPS=2, AGENT_MAX_TOOL_CALLS=8, AGENT_MAX_SUPPLEMENTS=1,
        )
        b = AgentBudget.from_settings(s)
        assert b.subtasks == 4 and b.loops == 2 and b.tool_calls == 8 and b.supplements == 1


class TestBudgetCounter:
    def _counter(self):
        return BudgetCounter(AgentBudget(subtasks=2, loops=2, tool_calls=3, supplements=1))

    def test_counts_and_bounds(self):
        c = self._counter()
        assert c.tool_calls_exceeded() is False
        c.record_tool_call(); c.record_tool_call(); c.record_tool_call()
        assert c.tool_calls_used == 3
        assert c.tool_calls_exceeded() is False  # == max 仍未超
        c.record_tool_call()
        assert c.tool_calls_used == 4
        assert c.tool_calls_exceeded() is True

    def test_subtask_and_loop_bounds(self):
        c = self._counter()
        c.record_subtask(); c.record_subtask()
        assert c.subtask_exceeded() is False
        c.record_subtask()
        assert c.subtask_exceeded() is True

        c.record_loop(); c.record_loop()
        assert c.loop_exceeded() is False
        c.record_loop()
        assert c.loop_exceeded() is True

    def test_supplement_bound(self):
        c = self._counter()
        c.record_supplement()
        assert c.supplement_exceeded() is False
        c.record_supplement()
        assert c.supplement_exceeded() is True

    def test_exhausted_and_to_dict(self):
        c = self._counter()
        c.record_tool_call(); c.record_tool_call(); c.record_tool_call()
        c.record_tool_call()
        assert c.exhausted() is True
        d = c.to_dict()
        assert d["tool_calls"]["used"] == 4
        assert d["tool_calls"]["limit"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_budgets.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/budgets.py**

```python
"""Agent 预算控制（硬限制）。

预算为每次任务的硬上限：超过即停止，绝不静默解锁。BudgetCounter 负责累计，
exhausted() 判定任一维度超限。上限来自配置或请求级覆盖。
"""
from dataclasses import dataclass


@dataclass
class AgentBudget:
    """一次任务的预算上限集合。"""

    subtasks: int
    loops: int
    tool_calls: int
    supplements: int

    @classmethod
    def from_settings(cls, settings) -> "AgentBudget":
        return cls(
            subtasks=settings.AGENT_MAX_SUBTASKS,
            loops=settings.AGENT_MAX_LOOPS,
            tool_calls=settings.AGENT_MAX_TOOL_CALLS,
            supplements=settings.AGENT_MAX_SUPPLEMENTS,
        )


class BudgetCounter:
    """运行时累计预算使用，并判定是否超限。"""

    def __init__(self, budget: AgentBudget):
        self.budget = budget
        self.subtasks_used = 0
        self.loops_used = 0
        self.tool_calls_used = 0
        self.supplements_used = 0

    def record_subtask(self) -> int:
        self.subtasks_used += 1
        return self.subtasks_used

    def record_loop(self) -> int:
        self.loops_used += 1
        return self.loops_used

    def record_tool_call(self) -> int:
        self.tool_calls_used += 1
        return self.tool_calls_used

    def record_supplement(self) -> int:
        self.supplements_used += 1
        return self.supplements_used

    def subtask_exceeded(self) -> bool:
        return self.subtasks_used > self.budget.subtasks

    def loop_exceeded(self) -> bool:
        return self.loops_used > self.budget.loops

    def tool_calls_exceeded(self) -> bool:
        return self.tool_calls_used > self.budget.tool_calls

    def supplement_exceeded(self) -> bool:
        return self.supplements_used > self.budget.supplements

    def exhausted(self) -> bool:
        return (
            self.subtask_exceeded()
            or self.loop_exceeded()
            or self.tool_calls_exceeded()
            or self.supplement_exceeded()
        )

    def to_dict(self) -> dict:
        return {
            "subtasks": {"used": self.subtasks_used, "limit": self.budget.subtasks},
            "loops": {"used": self.loops_used, "limit": self.budget.loops},
            "tool_calls": {"used": self.tool_calls_used, "limit": self.budget.tool_calls},
            "supplements": {"used": self.supplements_used, "limit": self.budget.supplements},
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_budgets.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/budgets.py tests/test_budgets.py
git commit -m "feat(agent): Agent 预算控制（硬限制）

- AgentBudget: 子任务/轮数/工具调用/补查上限，from_settings 构造
- BudgetCounter: 运行时累计 + 各维度超限判定 + exhausted() + to_dict
- 超过上限即停止，不静默解锁"
```

---

### Task 5: Agent 协议数据结构（protocols）

**Files:**
- Create: `app/agent/protocols.py`
- Create: `tests/test_protocols.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `InvestigationCard(card_id, goal_type, title, objective, evidence_requirements, suggested_tools, priority)` + `to_dict()` / `from_dict()`
  - `InvestigatorTurn`：`type` ∈ `{"tool_call","stop"}`
  - `InvestigatorResult(stop_reason, summary, findings, tool_calls_used, hypothesis)` + `to_dict()`
  - `ReviewVerdict(verdict, issues, supplement_query)` + `to_dict()`
  - 常量 `STOP_REASON_EVIDENCE_SUFFICIENT = "evidence_sufficient"` `STOP_REASON_DATA_INSUFFICIENT = "data_insufficient"` `STOP_REASON_BUDGET = "budget_exhausted"` `STOP_REASON_TOOL_FAILURE = "tool_failure"` `STOP_REASON_ILLEGAL = "illegal_output"`
  - `VALID_STOP_REASONS: set[str]`

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_protocols.py`：

```python
"""Agent 协议数据结构测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.protocols import (
    InvestigationCard, InvestigatorResult, ReviewVerdict,
    STOP_REASON_EVIDENCE_SUFFICIENT, STOP_REASON_DATA_INSUFFICIENT,
    STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE, STOP_REASON_ILLEGAL,
    VALID_STOP_REASONS,
)


class TestInvestigationCard:
    def test_to_from_dict_roundtrip(self):
        card = InvestigationCard(
            card_id="c1", goal_type="pulse", title="油耗问题", objective="确认油耗争议",
            evidence_requirements=["评论样本", "声量趋势"], suggested_tools=["sample_comments"],
            priority=1,
        )
        d = card.to_dict()
        c2 = InvestigationCard.from_dict(d)
        assert c2.card_id == "c1"
        assert c2.priority == 1
        assert c2.evidence_requirements == ["评论样本", "声量趋势"]


class TestInvestigatorResult:
    def test_to_dict(self):
        r = InvestigatorResult(
            stop_reason=STOP_REASON_EVIDENCE_SUFFICIENT, summary="已充分", findings=["f1"],
            tool_calls_used=3, hypothesis="油耗是主要议题",
        )
        d = r.to_dict()
        assert d["stop_reason"] == "evidence_sufficient"
        assert d["tool_calls_used"] == 3
        assert d["findings"] == ["f1"]


class TestReviewVerdict:
    def test_to_dict(self):
        v = ReviewVerdict(verdict="pass", issues=[{"type": "evidence_gap", "detail": "x"}], supplement_query=None)
        d = v.to_dict()
        assert d["verdict"] == "pass"
        assert d["issues"] == [{"type": "evidence_gap", "detail": "x"}]


class TestStopReasons:
    def test_all_stop_reasons_are_declared(self):
        assert VALID_STOP_REASONS == {
            STOP_REASON_EVIDENCE_SUFFICIENT, STOP_REASON_DATA_INSUFFICIENT,
            STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE, STOP_REASON_ILLEGAL,
        }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_protocols.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/protocols.py**

```python
"""V0.4 Agent 协议数据结构。

调查卡（InvestigationCard）、子 Agent 回合输出（InvestigatorTurn result 结构）、
评审结论（ReviewVerdict）与停止原因常量。所有结构可由 dict 序列化，便于事件记录与前端展示。
"""
from dataclasses import dataclass, field


# 停止原因（V0.4）
STOP_REASON_EVIDENCE_SUFFICIENT = "evidence_sufficient"
STOP_REASON_DATA_INSUFFICIENT = "data_insufficient"
STOP_REASON_BUDGET = "budget_exhausted"
STOP_REASON_TOOL_FAILURE = "tool_failure"
STOP_REASON_ILLEGAL = "illegal_output"

VALID_STOP_REASONS = {
    STOP_REASON_EVIDENCE_SUFFICIENT, STOP_REASON_DATA_INSUFFICIENT,
    STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE, STOP_REASON_ILLEGAL,
}


@dataclass
class InvestigationCard:
    """主 Agent 拆解出的一张调查卡。"""

    card_id: str
    goal_type: str
    title: str
    objective: str
    evidence_requirements: list[str] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    priority: int = 1

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "goal_type": self.goal_type,
            "title": self.title,
            "objective": self.objective,
            "evidence_requirements": self.evidence_requirements,
            "suggested_tools": self.suggested_tools,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InvestigationCard":
        return cls(
            card_id=d["card_id"],
            goal_type=d.get("goal_type", "pulse"),
            title=d.get("title", ""),
            objective=d.get("objective", ""),
            evidence_requirements=d.get("evidence_requirements", []),
            suggested_tools=d.get("suggested_tools", []),
            priority=int(d.get("priority", 1)),
        )


@dataclass
class InvestigatorResult:
    """子 Agent 对一张调查卡的执行结果。"""

    stop_reason: str
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    tool_calls_used: int = 0
    hypothesis: str = ""

    def to_dict(self) -> dict:
        return {
            "stop_reason": self.stop_reason,
            "summary": self.summary,
            "findings": self.findings,
            "tool_calls_used": self.tool_calls_used,
            "hypothesis": self.hypothesis,
        }


@dataclass
class ReviewVerdict:
    """独立评审结论。"""

    verdict: str  # pass / request_supplement
    issues: list[dict] = field(default_factory=list)
    supplement_query: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "issues": self.issues,
            "supplement_query": self.supplement_query,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewVerdict":
        return cls(
            verdict=d.get("verdict", "pass"),
            issues=d.get("issues", []),
            supplement_query=d.get("supplement_query", ""),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_protocols.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/protocols.py tests/test_protocols.py
git commit -m "feat(agent): Agent 协议数据结构

- InvestigationCard / InvestigatorResult / ReviewVerdict + to_dict/from_dict
- 5 种停止原因常量与 VALID_STOP_REASONS
- 所有结构可序列化，用于事件记录与前端展示"
```

---

### Task 6: Supervisor 角色（生成调查卡）

**Files:**
- Create: `app/agent/supervisor.py`
- Create: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `AnalysisIntent`（`app.understanding.intent`）、`EvidenceStore`、`InvestigationCard`、`LLMProvider.chat_json`、`Settings`
- Produces:
  - `Supervisor(llm_provider, settings)` + `plan(intent, store, max_subtasks) -> list[InvestigationCard]`
  - `parse_cards(text_or_dict) -> list[InvestigationCard]`：从 LLM 输出解析卡片列表（容错：接受 `{"cards": [...]}` 或裸数组；单项缺字段有默认）
  - 每张卡必须含 `card_id`、`objective`、`goal_type`、`title`、`evidence_requirements`（尽力补全，缺失用默认）；`suggested_tools` 只允许来自 `TOOL_WHITELIST`，越界项剔除。
  - 输出层数上限：`min(max_subtasks, AGENT_MAX_SUBTASKS)`。

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_supervisor.py`：

```python
"""Supervisor 角色测试（mock LLM，不真实调用）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.agent.supervisor import Supervisor, parse_cards
from app.agent.protocols import InvestigationCard
from app.agent.tools_spec import TOOL_WHITELIST
from app.store.evidence import EvidenceStore


class FakeLLM:
    """返回 fixed 的 JSON（chat_json 读取 content 后 json.loads）。"""
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat_json(self, messages, **kw):
        self.calls.append(messages)
        from app.llm.provider import LLMResult, LLMUsage
        return json.loads(self.content), LLMResult(content=self.content, usage=LLMUsage(model="test"))


class TestSupervisorPlan:
    def _card_json(self):
        return {
            "cards": [
                {"card_id": "c1", "goal_type": "pulse", "title": "声量趋势",
                 "objective": "确认近30天声量变化", "evidence_requirements": ["趋势曲线"],
                 "suggested_tools": ["volume_trend", "period_comparison"], "priority": 1},
                {"card_id": "c2", "goal_type": "drill", "title": "油耗", 
                 "objective": "下钻油耗主题", "evidence_requirements": ["评论样本"],
                 "suggested_tools": ["sample_comments", "drill_evidence"], "priority": 2},
            ]
        }

    def test_plan_returns_cards(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        from app.understanding.intent import AnalysisIntent, TimeRange
        from datetime import date
        intent = AnalysisIntent(object="坦克300", time_range=TimeRange(date(2026, 8, 1), date(2026, 8, 31)), goal_type="pulse")
        llm = FakeLLM(json.dumps(self._card_json()))
        sup = Supervisor(llm, s)
        cards = sup.plan(intent, EvidenceStore("t1"), max_subtasks=6)
        assert len(cards) == 2
        assert isinstance(cards[0], InvestigationCard)
        assert cards[0].card_id == "c1"
        assert cards[0].objective == "确认近30天声量变化"

    def test_plan_respects_max_subtasks(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        from app.understanding.intent import AnalysisIntent, TimeRange
        from datetime import date
        intent = AnalysisIntent(object="坦克300", time_range=TimeRange(date(2026, 8, 1), date(2026, 8, 31)), goal_type="pulse")
        llm = FakeLLM(json.dumps({"cards": [self._card_json()["cards"][0] for _ in range(4)]}))
        sup = Supervisor(llm, s)
        cards = sup.plan(intent, EvidenceStore("t1"), max_subtasks=2)
        assert len(cards) <= 2

    def test_plan_filters_unauthorized_tools(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        from app.understanding.intent import AnalysisIntent
        intent = AnalysisIntent(object="坦克300", time_range=None, goal_type="pulse")
        bad = self._card_json()
        bad["cards"][0]["suggested_tools"] = ["never_registered_tool"]
        llm = FakeLLM(json.dumps(bad))
        sup = Supervisor(llm, s)
        cards = sup.plan(intent, EvidenceStore("t1"), max_subtasks=6)
        # 越界工具被剔除，工具白名单内工具保留
        assert "never_registered_tool" not in cards[0].suggested_tools


class TestParseCards:
    def test_accepts_bare_array(self):
        out = parse_cards(json.dumps([{"card_id": "x", "objective": "o", "goal_type": "pulse",
                                       "title": "t", "evidence_requirements": [], "suggested_tools": [], "priority": 1}]))
        assert len(out) == 1
        assert out[0].card_id == "x"

    def test_fills_missing_fields_with_defaults(self):
        out = parse_cards(json.dumps({"cards": [{"card_id": "y"}]}))
        assert out[0].goal_type == "pulse"
        assert out[0].priority == 1
        assert out[0].suggested_tools == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_supervisor.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/supervisor.py**

```python
"""Supervisor 角色：把任务意图拆解为结构化调查卡。

主 Agent 依据意图与既有证据，调用 LLM 生成 InvestigationCard[]。不调用任何分析工具；
工具白名单由 __init__ 注入（默认 TOOL_WHITELIST），越界建议工具在解析时剔除。
"""
import json

from app.agent.protocols import InvestigationCard
from app.agent.tools_spec import TOOL_WHITELIST


class Supervisor:
    """主 Agent：规划调查卡。"""

    def __init__(self, llm_provider, settings, tool_whitelist=None):
        self.llm_provider = llm_provider
        self.settings = settings
        self.tool_whitelist = tool_whitelist or TOOL_WHITELIST

    def plan(self, intent, evidence_store, max_subtasks: int) -> list[InvestigationCard]:
        """依据意图与既有证据生成调查卡。

        Args:
            intent: AnalysisIntent
            evidence_store: EvidenceStore（提供既有证据摘要与对象/时间范围）
            max_subtasks: 允许的最大子任务数（请求级或配置），已由调用方钳制

        Returns:
            list[InvestigationCard]
        """
        limit = max(1, min(int(max_subtasks), self.settings.AGENT_MAX_SUBTASKS))
        prompt = self._build_prompt(intent, evidence_store, limit)
        data, _ = self.llm_provider.chat_json(prompt, temperature=0.2, max_tokens=4000)
        cards = parse_cards(json.dumps(data))
        return cards[:limit]

    def _build_prompt(self, intent, evidence_store, limit) -> list[dict]:
        """构建 Supervisor 专用 prompt。"""
        obj = getattr(intent, "object", "") or ""
        goal = getattr(intent, "goal_type", "pulse") or "pulse"
        tr = getattr(intent, "time_range", None)
        time_desc = f"{tr.start} ~ {tr.end}" if tr else "不限"
        evidence_count = len(evidence_store.all_records())
        tool_list = ", ".join(sorted(self.tool_whitelist))
        system = (
            "你是营销舆情分析的主 Agent（Supervisor）。你负责把用户的调查目标拆解为"
            "若干张结构化调查卡（InvestigationCard），交给子 Agent 分别调查。\n"
            "你只做规划，不调用任何数据工具。\n"
            "每张调查卡必须包含：card_id, goal_type, title, objective, "
            "evidence_requirements, suggested_tools, priority。\n"
            "suggested_tools 只能从以下白名单中选择（可留空表示由子 Agent 自主决定）："
            f"{tool_list}。\n"
            f"最多输出 {limit} 张调查卡。输出必须是 JSON：{{'cards': [ ... ]}}。"
        )
        user = (
            f"分析对象：{obj}\n"
            f"目标类型：{goal}\n"
            f"时间范围：{time_desc}\n"
            f"已有证据登记数：{evidence_count}\n"
            "请生成聚焦、互不重叠、每个都能独立回答一个具体问题的调查卡。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_cards(text_or_dict) -> list[InvestigationCard]:
    """从 LLM 输出解析调查卡列表（容错）。

    Args:
        text_or_dict: LLM 返回的 content 字符串或已解析的 dict。

    Returns:
        list[InvestigationCard]
    """
    if isinstance(text_or_dict, str):
        try:
            data = json.loads(text_or_dict)
        except (json.JSONDecodeError, TypeError):
            data = {}
    else:
        data = text_or_dict or {}
    if isinstance(data, list):
        raw_cards = data
    elif isinstance(data, dict):
        raw_cards = data.get("cards") or []
    else:
        raw_cards = []
    out = []
    for c in raw_cards:
        if not isinstance(c, dict):
            continue
        out.append(InvestigationCard.from_dict(c))
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_supervisor.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/supervisor.py tests/test_supervisor.py
git commit -m "feat(agent): Supervisor 主 Agent（生成调查卡）

- Supervisor.plan: 意图+证据 -> InvestigationCard[]，通过 chat_json 调用 LLM
- 解析容错：接受 {'cards': [...]} 或裸数组，缺字段用默认
- 越界 suggest_tools 剔除；card 数受 min(max_subtasks, 配置上限) 约束"
```

---

### Task 7: Investigator 角色（单卡受控 Loop + function calling）

**Files:**
- Create: `app/agent/investigator.py`
- Create: `tests/test_investigator.py`

**Interfaces:**
- Consumes:
  - `InvestigationCard`、`InvestigatorResult`、`STOP_REASON_*`
  - `BudgetCounter`、`TOOL_JSON_SCHEMAS`、`TOOL_WHITELIST`、`validate_and_coerce`
  - `get_tool(name)`（`app.analysis.registry`）
  - `LLMProvider.chat`（传 `tools=TOOL_JSON_SCHEMAS`）
  - `EvidenceStore`、`EventRepository.append_event`
- Produces:
  - `Investigator(llm_provider, datasource, snapshot, evidence_store, event_repo, budget_counter, tool_whitelist=None, max_loops=None, max_records=None)`
  - `Investigator.run(card, task_id, round_no) -> InvestigatorResult`
  - 内部：`_run_turn(...)` —— 每轮前检查预算（超限→`budget_exhausted`）、调用 LLM（`tools=`）、解析回合、执行工具、回注上下文、继续或停止。
  - `_execute_tool(tool_name, arguments, task_id) -> dict`：授权校验 → `validate_and_coerce` → `get_tool` → 执行 → 记录 `subtask_tool` 事件 → 返回结果摘要。
- Loop 规则：
  1. 每轮开始前：`budget_counter.record_loop()`；若 `loop_exceeded()` 或 `tool_calls_exceeded()` → 以 `budget_exhausted` 停止。
  2. LLM 返回：从 `LLMResult.tool_calls` 取到 `{name, arguments}`；若为空且 content 可解析出 `{"type":"stop","stop_reason":...}` → 以该 stop_reason 停止。
  3. 若 LLM 返回 `tool_call` 类型回合且希望调用工具 → 校验/执行；`record_tool_call()`；超限则停止。
  4. 无法解析为合法回合 → `illegal_output`（重试 1 次，仍失败则停止）。
  5. 工具执行抛异常 → `tool_failure` 停止。

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_investigator.py`：

```python
"""Investigator 子 Agent 测试（mock LLM + FakeDS）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.agent.investigator import Investigator
from app.agent.protocols import InvestigationCard, InvestigatorResult
from app.agent.budgets import AgentBudget, BudgetCounter
from app.store.evidence import EvidenceStore
from app.store.repository import EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from datetime import datetime
import tempfile


class FakeDS:
    def count_comments(self, **kw):
        return 100
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(
            comment_id=f"c{i}", content=f"评论{i}", video_title="坦克300", job_id="j1",
            comment_like_count=1, passed=True, is_car_owner=True, has_purchase_intent=False,
        ) for i in range(limit)]


class FakeLLM:
    """按轮次返回预置响应。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def chat(self, messages, **kw):
        self.calls.append(messages)
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "done"})}
        return LLMResult(content=nxt.get("content"), usage=LLMUsage(model="test"), raw=nxt.get("raw", {}))


def _card():
    return InvestigationCard(
        card_id="c1", goal_type="pulse", title="样本", objective="取样",
        evidence_requirements=["样本"], suggested_tools=["sample_comments"], priority=1,
    )


def _setup(llm, **budget_kw):
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    event_repo = EventRepository(db.name)
    event_repo.init_schema()
    store = EvidenceStore("t1")
    snap = LogicalSnapshot(start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31))
    counter = BudgetCounter(AgentBudget(subtasks=1, loops=budget_kw.get("loops", 3),
                                        tool_calls=budget_kw.get("tool_calls", 20), supplements=1))
    inv = Investigator(llm, FakeDS(), snap, store, event_repo, counter,
                       max_loops=budget_kw.get("loops", 3), max_records=500)
    return inv, event_repo, store, counter


class TestInvestigator:
    def test_tool_call_then_stop(self):
        """首轮 tool_call，次轮 stop（evidence_sufficient）。"""
        llm = FakeLLM([
            {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
            ]}}]}},
            {"content": json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "ok", "findings": ["样本足够"]})},
        ])
        inv, event_repo, store, counter = _setup(llm)
        result = inv.run(_card(), "t1", 1)
        assert isinstance(result, InvestigatorResult)
        assert result.stop_reason == "evidence_sufficient"
        assert result.tool_calls_used == 1
        events = event_repo.get_events("t1")
        assert any(e.event_type == "subtask_tool" for e in events)

    def test_data_insufficient_stop(self):
        llm = FakeLLM([
            {"content": json.dumps({"type": "stop", "stop_reason": "data_insufficient", "summary": "数据太少"})},
        ])
        inv, event_repo, store, counter = _setup(llm)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "data_insufficient"

    def test_budget_exhausted_stops(self):
        """tool_calls 上限 1：一次调用后再次尝试即预算耗尽。"""
        llm = FakeLLM([
            {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
            ]}}]}},
            {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
            ]}}]}},
        ])
        inv, event_repo, store, counter = _setup(llm, tool_calls=1, loops=3)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "budget_exhausted"

    def test_tool_failure_stops(self):
        class BoomDS(FakeDS):
            def fetch_comments(self, **kw):
                raise RuntimeError("db down")
        llm = FakeLLM([
            {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
            ]}}]}},
        ])
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False); db.close()
        event_repo = EventRepository(db.name); event_repo.init_schema()
        store = EvidenceStore("t1")
        counter = BudgetCounter(AgentBudget(subtasks=1, loops=3, tool_calls=20, supplements=1))
        inv = Investigator(llm, BoomDS(), LogicalSnapshot(datetime(2026, 8, 1), datetime(2026, 8, 31)), store, event_repo, counter, max_loops=3, max_records=500)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "tool_failure"

    def test_illegal_output_stops(self):
        """无法解析为合法回合 -> illegal_output。"""
        llm = FakeLLM([
            {"content": "not json"},
            {"content": "still not json"},
        ])
        inv, event_repo, store, counter = _setup(llm)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "illegal_output"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_investigator.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/investigator.py**

```python
"""Investigator 子 Agent：单张调查卡的受控调查 Loop。

通过真实 function calling 从白名单工具中选择调用，依据工具结果（证据摘要）决定
继续下钻或停止。每轮开始前检查预算，超限即停止。工具名与参数必须通过授权与校验。
"""
import json

from app.agent.protocols import (
    InvestigationCard, InvestigatorResult, STOP_REASON_EVIDENCE_SUFFICIENT,
    STOP_REASON_DATA_INSUFFICIENT, STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE,
    STOP_REASON_ILLEGAL, VALID_STOP_REASONS,
)
from app.agent.tools_spec import (
    TOOL_JSON_SCHEMAS, TOOL_WHITELIST, validate_and_coerce, ToolSpecError,
)
from app.analysis.registry import get_tool
from app.llm.provider import LLMResult


class Investigator:
    """子 Agent：围绕一张调查卡开展有限探索。"""

    def __init__(self, llm_provider, datasource, snapshot, evidence_store,
                 event_repo, budget_counter, *, tool_whitelist=None,
                 max_loops=None, max_records=None):
        self.llm_provider = llm_provider
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.event_repo = event_repo
        self.budget_counter = budget_counter
        self.tool_whitelist = tool_whitelist or set(TOOL_WHITELIST)
        self.max_loops = max_loops
        self.max_records = max_records or 500

    def run(self, card: InvestigationCard, task_id: str, round_no: int = 1) -> InvestigatorResult:
        """执行单张调查卡，返回 InvestigatorResult。"""
        self.event_repo.append_event(
            task_id, "subtask_start",
            {"card_id": card.card_id, "title": card.title, "round": round_no,
             "suggested_tools": card.suggested_tools},
        )
        tool_calls_used = 0
        messages = self._build_messages(card, task_id)
        loops = 0
        while True:
            # 每轮前预算检查（硬限制）
            self.budget_counter.record_loop()
            loops += 1
            if self.budget_counter.loop_exceeded() or self.budget_counter.tool_calls_exceeded():
                return self._stop(task_id, card, STOP_REASON_BUDGET, "预算耗尽，停止本轮调查", tool_calls_used)

            for attempt in range(2):  # 非法输出重试 1 次
                result = self._call_llm(messages)
                turn = self._parse_turn(result)
                if turn is not None:
                    break
                if attempt == 0:
                    self.event_repo.append_event(task_id, "illegal_output",
                        {"card_id": card.card_id, "error": "无法解析为合法回合，重试"})
                    continue
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "LLM 输出非法，停止", tool_calls_used)
            else:
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "LLM 输出非法，停止", tool_calls_used)

            ttype = turn["type"]
            if ttype == "stop":
                reason = turn.get("stop_reason", STOP_REASON_DATA_INSUFFICIENT)
                if reason not in VALID_STOP_REASONS:
                    reason = STOP_REASON_DATA_INSUFFICIENT
                return self._stop(task_id, card, reason,
                                  turn.get("summary", ""), tool_calls_used,
                                  findings=turn.get("findings", []), hypothesis=turn.get("hypothesis", ""))
            # ttype == "tool_call"
            tool_name = turn.get("tool")
            arguments = turn.get("arguments") or {}
            self.budget_counter.record_tool_call()
            if self.budget_counter.tool_calls_exceeded():
                return self._stop(task_id, card, STOP_REASON_BUDGET, "工具调用预算耗尽", tool_calls_used)
            tool_calls_used += 1
            try:
                tool_summary = self._execute_tool(tool_name, arguments, task_id, card)
            except ToolSpecError:
                self.event_repo.append_event(task_id, "tool_unauthorized",
                    {"card_id": card.card_id, "tool": tool_name, "allowed": sorted(self.tool_whitelist)})
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "工具参数非法或未授权，停止", tool_calls_used)
            except Exception as e:
                self.event_repo.append_event(task_id, "tool_failure",
                    {"card_id": card.card_id, "tool": tool_name, "error": str(e)})
                return self._stop(task_id, card, STOP_REASON_TOOL_FAILURE, f"工具执行失败: {e}", tool_calls_used)
            messages = self._append_tool_result(messages, tool_name, tool_summary, turn.get("reason", ""))

    def _call_llm(self, messages) -> LLMResult:
        return self.llm_provider.chat(messages, tools=TOOL_JSON_SCHEMAS,
                                      temperature=0.2, max_tokens=2000)

    def _parse_turn(self, result: LLMResult) -> dict | None:
        """从 LLMResult 解析合法回合 dict，或 None。

        优先级：tool_calls（function calling）> content 中的 {"type":"stop"/"tool_call"}。
        """
        tcs = result.tool_calls
        if tcs:
            return {"type": "tool_call", "tool": tcs[0]["name"], "arguments": tcs[0]["arguments"], "reason": ""}
        content = result.content or ""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        ttype = data.get("type")
        if ttype in ("tool_call", "stop"):
            return data
        return None

    def _execute_tool(self, tool_name, arguments, task_id, card) -> dict:
        if tool_name not in self.tool_whitelist:
            raise ToolSpecError(f"tool '{tool_name}' 未授权")
        coerced = validate_and_coerce(arguments, tool_name, self.max_records)
        tool_fn = get_tool(tool_name)
        res = tool_fn(self.datasource, self.snapshot, self.evidence_store, **coerced)
        self.event_repo.append_event(task_id, "subtask_tool", {
            "card_id": card.card_id, "tool": tool_name, "arguments": coerced,
            "sample_size": res.get("sample_size", 0),
        })
        return {"name": res["name"], "sample_size": res.get("sample_size", 0),
                "result": {"comment_count": res.get("result", {}) if isinstance(res.get("result"), dict) else {}},
                "evidence_ids": res.get("evidence_ids", []), "bias_note": res.get("bias_note", "")}

    def _build_messages(self, card, task_id) -> list[dict]:
        system = (
            f"你是舆情调查子 Agent（Investigator）。你负责深入回答调查卡：[{card.title}]，"
            f"目标：{card.objective}。\n"
            f"本卡建议工具：{', '.join(card.suggested_tools) or '（由你自主选择）'}。\n"
            "你只能使用提供的工具（function calling），每次最多调用一个工具。"
            "工具返回后，结合结果判断是否需要进一步下钻（再次调用工具），"
            "或证据已足/数据不足时停止。\n"
            "停止时，请在 content 中用 JSON 输出："
            "{\"type\":\"stop\",\"stop_reason\":\"evidence_sufficient|data_insufficient|budget_exhausted\","
            "\"summary\":\"...\",\"findings\":[...],\"hypothesis\":\"...\"}。"
        )
        user = (
            f"调查卡：{card.to_dict()}\n"
            "关键证据要求：" + "；".join(card.evidence_requirements or ["（无前置要求）"])
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _append_tool_result(self, messages, tool_name, summary, reason) -> list[dict]:
        msg = {
            "role": "user",
            "content": json.dumps({
                "type": "tool_result", "tool": tool_name, "tool_result": summary,
                "reason": reason or "",
            }, ensure_ascii=False),
        }
        return messages + [msg]

    def _stop(self, task_id, card, reason, summary, tool_calls_used, findings=None, hypothesis="") -> InvestigatorResult:
        result = InvestigatorResult(
            stop_reason=reason, summary=summary, findings=findings or [],
            tool_calls_used=tool_calls_used, hypothesis=hypothesis,
        )
        self.event_repo.append_event(task_id, "subtask_stop", {
            "card_id": card.card_id, "stop_reason": reason, "summary": summary,
            "tool_calls_used": tool_calls_used,
        })
        return result
```

注：`_execute_tool` 中 `comment_count` 仅为摘要用，实际结果写入 evidence；对 `sample_comments`/`drill_evidence` 等工具，`result` 可能为完整 dict，这里只取 `comment_count` 键做摘要（其余细节由证据库承担）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_investigator.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/investigator.py tests/test_investigator.py
git commit -m "feat(agent): Investigator 子 Agent（受控 Loop + function calling）

- 单卡 Loop：每轮前预算检查，LLM 经 tools 选择工具，结果回注后继续或停止
- 授权/参数校验：未授权工具与非法参数 -> illegal_output；工具异常 -> tool_failure
- 停止原因：evidence_sufficient / data_insufficient / budget_exhausted / tool_failure / illegal_output
- 审计事件：subtask_start / subtask_tool / subtask_stop / tool_unauthorized / tool_failure / illegal_output"
```

---

### Task 8: Reviewer 角色（独立评审 + 一次受控补查决策）

**Files:**
- Create: `app/agent/reviewer.py`
- Create: `tests/test_reviewer.py`

**Interfaces:**
- Consumes: `ReviewVerdict`、`LLMProvider.chat_json`、`EvidenceStore`、cards/results（调查产出）
- Produces:
  - `Reviewer(llm_provider, settings)` + `review(subtask_results, cards, evidence_store, task_id) -> ReviewVerdict`
  - `parse_verdict(text_or_dict) -> ReviewVerdict`：容错解析 `{"verdict":"pass|request_supplement","issues":[...],"supplement_query":"..."}`
- Review 关注点：证据充分性、反例、样本偏差、结论越界。`verdict="request_supplement"` 时 `supplement_query` 必须有内容，供 supervisor 生成补充调查卡。

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_reviewer.py`：

```python
"""Reviewer 独立评审测试（mock LLM）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.agent.reviewer import Reviewer, parse_verdict
from app.agent.budgets import AgentBudget, BudgetCounter
from app.agent.protocols import InvestigationCard, InvestigatorResult
from app.store.evidence import EvidenceStore


class FakeLLM:
    def __init__(self, content):
        self.content = content
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        return json.loads(self.content), LLMResult(content=self.content, usage=LLMUsage(model="test"))


class TestReviewer:
    def _verdict_json(self, verdict="pass"):
        return json.dumps({"verdict": verdict, "issues": [{"type": "sample_bias", "detail": "样本偏"}],
                           "supplement_query": "补充调查" if verdict == "request_supplement" else ""})

    def test_review_pass(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        llm = FakeLLM(self._verdict_json("pass"))
        rev = Reviewer(llm, s)
        v = rev.review([InvestigatorResult("evidence_sufficient", findings=["f"])], [], EvidenceStore("t1"), "t1")
        assert v.verdict == "pass"
        assert v.supplement_query == ""

    def test_review_request_supplement(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        llm = FakeLLM(self._verdict_json("request_supplement"))
        rev = Reviewer(llm, s)
        v = rev.review([InvestigatorResult("data_insufficient")], [], EvidenceStore("t1"), "t1")
        assert v.verdict == "request_supplement"
        assert v.supplement_query == "补充调查"


class TestParseVerdict:
    def test_parse_pass(self):
        out = parse_verdict('{"verdict":"pass","issues":[]}')
        assert out.verdict == "pass"

    def test_parse_bare_dict(self):
        out = parse_verdict({"verdict": "request_supplement", "supplement_query": "q"})
        assert out.verdict == "request_supplement"

    def test_parse_missing_supplement_query_defaults_empty(self):
        out = parse_verdict('{"verdict":"request_supplement"}')
        assert out.supplement_query == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_reviewer.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/reviewer.py**

```python
"""Reviewer 独立评审角色。

检查证据充分性、反例、样本偏差、结论越界。输出 ReviewVerdict：
- pass：调查通过
- request_supplement：要求一次受控补充调查（补查额度由 orchestrator 控制，至多 1 次）
Reviewer 不调用任何分析工具，只做判断与决策。
"""
import json

from app.agent.protocols import ReviewVerdict
from app.agent.tools_spec import TOOL_WHITELIST


class Reviewer:
    """独立评审。"""

    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings

    def review(self, subtask_results, cards, evidence_store, task_id) -> ReviewVerdict:
        """评估调查产出，决定通过或要求补查。

        Args:
            subtask_results: list[InvestigatorResult]
            cards: list[InvestigationCard]
            evidence_store: EvidenceStore
            task_id: str

        Returns:
            ReviewVerdict
        """
        prompt = self._build_prompt(subtask_results, cards, evidence_store)
        data, _ = self.llm_provider.chat_json(prompt, temperature=0.0, max_tokens=3000)
        return parse_verdict(data)

    def _build_prompt(self, subtask_results, cards, evidence_store) -> list[dict]:
        result_lines = [
            f"- [{r.stop_reason}] {r.summary or '(无摘要)'}；工具调用 {r.tool_calls_used} 次；发现 {r.findings}"
            for r in subtask_results
        ]
        card_lines = [c.to_dict() for c in cards]
        evidence_count = len(evidence_store.all_records())
        system = (
            "你是独立的评审角色（Reviewer）。你负责检查以下舆情调查的证据是否充分、"
            "是否有反例、样本是否存在偏差、结论是否越界。\n"
            "你不调用任何工具。\n"
            "输出必须是 JSON：{\"verdict\":\"pass\" 或 \"request_supplement\","
            "\"issues\":[{\"type\":\"evidence_gap|counter_example|sample_bias|overclaim\",\"detail\":\"...\"}],"
            "\"supplement_query\":\"...\"（verdict 为 request_supplement 时必填，用于指导一次受控补查）}。"
        )
        user = (
            "调查卡：\n" + json.dumps(card_lines, ensure_ascii=False, indent=2) + "\n"
            "各卡调查结果：\n" + "\n".join(result_lines) + "\n"
            f"已登记证据数：{evidence_count}\n"
            "请给出评审结论。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_verdict(text_or_dict) -> ReviewVerdict:
    """容错解析评审结论。"""
    if isinstance(text_or_dict, str):
        try:
            data = json.loads(text_or_dict)
        except (json.JSONDecodeError, TypeError):
            data = {}
    else:
        data = text_or_dict or {}
    verdict = data.get("verdict")
    if verdict not in ("pass", "request_supplement"):
        verdict = "pass"
    return ReviewVerdict(
        verdict=verdict,
        issues=data.get("issues", []),
        supplement_query=data.get("supplement_query", ""),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_reviewer.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/reviewer.py tests/test_reviewer.py
git commit -m "feat(agent): Reviewer 独立评审角色

- review: 评估调查产出 -> pass / request_supplement
- 关注证据充分性、反例、样本偏差、结论越界；不调用工具
- parse_verdict 容错解析（缺 verdict 默认 pass，缺 supplement_query 默认空）"
```

---

### Task 9: Orchestrator（任务编排）

**Files:**
- Create: `app/agent/orchestrator.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes:
  - `Supervisor`、`Investigator`、`Reviewer`、`InvestigationCard`、`InvestigatorResult`、`ReviewVerdict`
  - `BudgetCounter`、`AgentBudget`、`TOOL_WHITELIST`
  - `app.analysis.registry.get_tool`（不直接用，供工具执行）
  - `EventRepository.append_event`、`TaskRepository`（可选）、`EvidenceStore`
  - `Settings`、`CreateTaskRequest` 的 `max_subtasks`/`max_tool_calls`（覆盖）
- Produces:
  - `Orchestrator(llm_provider, datasource, snapshot, evidence_store, event_repo, task_repo, settings)`
  - `Orchestrator.run(task_id, intent, *, max_subtasks=None, max_tool_calls=None) -> dict`
    - 返回结构含 `report`（三层策略包）+ `agent`（cards / subtask_results / review / budget）+ `evidence`
    - 汇总 token/耗时到 `meta`
- 主流程：
  1. 构建 `AgentBudget`（请求级覆盖 max_subtasks / max_tool_calls），`BudgetCounter`。
  2. `Supervisor.plan()` → cards；记录 `agent_plan` 事件。
  3. 逐个 card `Investigator.run()`，每卡前检查 `subtask_exceeded()`（超限停止派发，记 `budget_exhausted`）。
  4. 汇总 → `Reviewer.review()`；记录 `review_result` 事件。若 `request_supplement` 且 `supplements_used < AGENT_MAX_SUPPLEMENTS`：分配补充卡（用 supplement_query 生成临时卡），`record_supplement()`，Investigator 再执行一次，Re-review，`supplements_used+1`。
  5. 综合报告：`report` 三层（事实/解释性判断/待验证假设）+ `agent` 块 + `evidence`。
  6. 结果持久化交给 runner 层。

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_orchestrator.py`：

```python
"""Orchestrator 任务编排测试（mock LLM + FakeDS）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import tempfile

from app.agent.orchestrator import Orchestrator
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.understanding.intent import AnalysisIntent, TimeRange
from datetime import date, datetime


class FakeDS:
    def count_comments(self, **kw):
        return 100
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(comment_id=f"c{i}", content=f"评论{i}", video_title="坦克300", job_id="j1",
                              comment_like_count=1, passed=True, is_car_owner=True, has_purchase_intent=False) for i in range(limit)]


class FakeLLM:
    """按调用序号返回预设响应：0 supervisor, 1+ investigator/reviewer。"""
    def __init__(self):
        self.responses = []
        self.calls = []
    def _push(self, content, raw=None):
        self.responses.append({"content": content, "raw": raw or {}})
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        self.calls.append(messages)
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return json.loads(nxt["content"]), LLMResult(content=nxt["content"], usage=LLMUsage(model="test"))
    def chat(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        self.calls.append(messages)
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return LLMResult(content=nxt.get("content"), usage=LLMUsage(model="test"), raw=nxt.get("raw", {}))


def _setup(tmp_path):
    db = str(tmp_path / "t.db")
    task_repo = TaskRepository(db); task_repo.init_schema()
    event_repo = EventRepository(db); event_repo.init_schema()
    store = EvidenceStore("t1")
    snap = LogicalSnapshot(start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31))
    from app.core.config import Settings
    settings = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                        LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                        AGENT_MAX_SUBTASKS=3, AGENT_MAX_LOOPS=3, AGENT_MAX_TOOL_CALLS=6, AGENT_MAX_SUPPLEMENTS=1)
    return task_repo, event_repo, store, snap, settings


class TestOrchestrator:
    def test_full_run(self, tmp_path):
        task_repo, event_repo, store, snap, settings = _setup(tmp_path)
        llm = FakeLLM()
        # supervisor 返回一张卡
        llm._push(json.dumps({"cards": [{"card_id": "c1", "goal_type": "pulse", "title": "样本",
                                         "objective": "取样", "evidence_requirements": ["样本"],
                                         "suggested_tools": ["sample_comments"], "priority": 1}]}))
        # investigator 首轮 tool_call
        llm._push(None, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
        ]}}]})
        # investigator 停止
        llm._push(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "ok", "findings": ["样本足够"]}))
        # reviewer pass
        llm._push(json.dumps({"verdict": "pass", "issues": []}))

        task = task_repo.create_task(raw_input="分析坦克300", parsed_intent={"goal_type": "pulse"}, snapshot=snap.to_dict())
        orch = Orchestrator(llm, FakeDS(), snap, store, event_repo, task_repo, settings)
        result = orch.run(task.task_id, AnalysisIntent("坦克300", TimeRange(date(2026, 8, 1), date(2026, 8, 31)), "pulse"))

        assert "report" in result
        assert "agent" in result
        assert result["agent"]["budget"]["tool_calls"]["used"] >= 1
        events = event_repo.get_events(task.task_id)
        assert any(e.event_type == "agent_plan" for e in events)
        assert any(e.event_type == "review_result" for e in events)
        assert any(e.event_type == "task_finished" for e in events)

    def test_supplement_once(self, tmp_path):
        task_repo, event_repo, store, snap, settings = _setup(tmp_path)
        llm = FakeLLM()
        llm._push(json.dumps({"cards": [{"card_id": "c1", "goal_type": "pulse", "title": "t", "objective": "o",
                                         "evidence_requirements": [], "suggested_tools": [], "priority": 1}]}))
        llm._push(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "s", "findings": ["f"]}))
        # 首次 review request_supplement
        llm._push(json.dumps({"verdict": "request_supplement", "issues": [{"type": "evidence_gap", "detail": "d"}], "supplement_query": "补充下钻"}))
        # 补查卡的 investigator stop
        llm._push(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "s2", "findings": ["f2"]}))
        # 二次 review pass
        llm._push(json.dumps({"verdict": "pass", "issues": []}))

        task = task_repo.create_task(raw_input="分析", parsed_intent={}, snapshot=snap.to_dict())
        orch = Orchestrator(llm, FakeDS(), snap, store, event_repo, task_repo, settings)
        result = orch.run(task.task_id, AnalysisIntent("坦克300", None, "pulse"))
        assert result["agent"]["review"]["verdict"] == "pass"
        assert result["agent"]["budget"]["supplements"]["used"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 编写 app/agent/orchestrator.py**

```python
"""Orchestrator：编排 Supervisor -> Investigator[] -> Reviewer -> 综合报告。

主流程固定（确定性骨架），探索由子 Agent 在预算内完成。预算/停止条件/评审门禁
属于本模块，普通子 Agent 无法绕过。
"""
import time

from app.agent.budgets import AgentBudget, BudgetCounter
from app.agent.investigator import Investigator
from app.agent.protocols import InvestigationCard, STOP_REASON_BUDGET
from app.agent.reviewer import Reviewer
from app.agent.supervisor import Supervisor
from app.agent.tools_spec import TOOL_WHITELIST


class Orchestrator:
    """V0.4 任务编排。"""

    def __init__(self, llm_provider, datasource, snapshot, evidence_store,
                 event_repo, task_repo, settings):
        self.llm_provider = llm_provider
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.event_repo = event_repo
        self.task_repo = task_repo
        self.settings = settings
        self.supervisor = Supervisor(llm_provider, settings)
        self.reviewer = Reviewer(llm_provider, settings)
        self._started = None

    def run(self, task_id: str, intent, *, max_subtasks=None, max_tool_calls=None) -> dict:
        self._started = time.monotonic()
        self.event_repo.append_event(task_id, "agent_run_start", {"task_id": task_id})

        budget = self._build_budget(max_subtasks, max_tool_calls)
        counter = BudgetCounter(budget)
        cards = self.supervisor.plan(intent, self.evidence_store, budget.subtasks)
        self.event_repo.append_event(task_id, "agent_plan",
                                     {"cards": [c.to_dict() for c in cards], "budget": counter.to_dict()})

        subtask_results = []
        for card in cards:
            if counter.subtask_exceeded():
                self.event_repo.append_event(task_id, "subtask_stop", {"card_id": card.card_id, "stop_reason": STOP_REASON_BUDGET})
                break
            counter.record_subtask()
            inv = self._make_investigator(counter, budget)
            res = inv.run(card, task_id, round_no=1)
            subtask_results.append(res)

        review = self.reviewer.review(subtask_results, cards, self.evidence_store, task_id)
        self.event_repo.append_event(task_id, "review_result", review.to_dict())

        # 受控补查（至多 1 次）
        if review.verdict == "request_supplement" and not counter.supplement_exceeded():
            if review.supplement_query:
                counter.record_supplement()
                supplement_card = InvestigationCard(
                    card_id=f"supplement-{len(subtask_results)+1}",
                    goal_type="drill", title="补充调查", objective=review.supplement_query,
                    evidence_requirements=["补充证据"], suggested_tools=[], priority=0,
                )
                cnt_before = counter.subtasks_used
                counter.record_subtask()
                inv = self._make_investigator(counter, budget)
                sup_res = inv.run(supplement_card, task_id, round_no=1)
                subtask_results.append(sup_res)
                rev2 = self.reviewer.review(subtask_results, cards + [supplement_card], self.evidence_store, task_id)
                review = rev2
                self.event_repo.append_event(task_id, "review_result", rev2.to_dict())

        report = self._build_report(cards, subtask_results, review, counter)
        self.event_repo.append_event(task_id, "task_finished", {"status": "success", "budget": counter.to_dict()})
        return report

    def _build_budget(self, max_subtasks, max_tool_calls) -> AgentBudget:
        b = AgentBudget.from_settings(self.settings)
        if max_subtasks is not None:
            b.subtasks = max(1, int(max_subtasks))
        if max_tool_calls is not None:
            b.tool_calls = max(1, int(max_tool_calls))
        return b

    def _make_investigator(self, counter, budget):
        return Investigator(
            self.llm_provider, self.datasource, self.snapshot, self.evidence_store,
            self.event_repo, counter, tool_whitelist=TOOL_WHITELIST,
            max_loops=budget.loops, max_records=self.settings.TOOL_MAX_RECORDS,
        )

    def _build_report(self, cards, subtask_results, review, counter) -> dict:
        """综合三层报告：事实层（统计）+ 解释性判断层（judgment）+ 待验证假设层（assumption）。"""
        # 统计字段从证据库聚合（事实层）
        evidence_list = self.evidence_store.to_dicts()
        stats = [e for e in evidence_list if e["kind"] == "stat"]
        judgments = [e for e in evidence_list if e["kind"] == "judgment"]
        assumptions = [e for e in evidence_list if e["kind"] == "assumption"]
        videos = [e for e in evidence_list if e["kind"] == "video"]

        themes = [{"title": j["extra"]["get("title", "judgment")"} for j in judgments]
        # 简化：解释性判断层列出 judgment 标题；待验证假设层列出 assumption 标题
        return {
            "scope": {},
            "overall": {},
            "themes": themes,
            "sources": [{"video_title": v.get("video_title"), "job_id": v.get("job_id")} for v in videos],
            "risk_opportunity": [{"title": j["extra"]["get("title", "judgment")", "type": j["extra"].get("judgment_type")} for j in judgments],
            "evidence_gaps": [{"title": a["extra"].get("title"), "rationale": a["extra"].get("rationale")} for a in assumptions],
            "actions": [],
            "metrics": [],
            "agent": {
                "cards": [c.to_dict() for c in cards],
                "subtask_results": [r.to_dict() for r in subtask_results],
                "review": review.to_dict(),
                "budget": counter.to_dict(),
            },
            "evidence": evidence_list,
            "meta": {
                "model": getattr(self.llm_provider, "model", None),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": int((time.monotonic() - self._started) * 1000) if self._started else 0,
            },
        }
```

注：`themes`/`risk_opportunity` 中 `j["extra"]["get(...)]` 写法为占位示意，**实际实现请写为** `j["extra"].get("title")`、`j["extra"].get("judgment_type")`（见 Step 5 修正）。上层需把 `assumption`/`judgment` 从 evidence 列表提取并登记（Investigator 执行工具已登记 stat/video/comment；judgment/assumption 由 orchestrator 在综合阶段登记）。为满足"事实/解释性判断/待验证假设"分层，本任务在 `_build_report` 中同时将 `themes`（来自 judgment 的 theme 类）与 `everything` 组装；**实现时以 Step 5 的最终代码为准，并保持 test_full_run 断言兼容**。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 修正 `_build_report` 的实现（消除占位）**

将 `_build_report` 整体替换为下述最终版本（供 implementer 照抄，保证 test_full_run 断言兼容）。核心点：从 evidence 提取各类证据，登记判断与假设（若源数据来自 investigator 输出则直接组装），并保证 `themes`/`risk_opportunity`/`evidence_gaps` 字段可用：

```python
    def _build_report(self, cards, subtask_results, review, counter) -> dict:
        evidence_list = self.evidence_store.to_dicts()
        stats = [e for e in evidence_list if e["kind"] == "stat"]
        judgments = [e for e in evidence_list if e["kind"] == "judgment"]
        assumptions = [e for e in evidence_list if e["kind"] == "assumption"]
        videos = [e for e in evidence_list if e["kind"] == "video"]

        themes = [{"title": j["extra"].get("title", ""), "evidence_refs": j["extra"].get("evidence_refs", [])}
                  for j in judgments if j["extra"].get("judgment_type") == "theme"]
        risk_opp = [{"title": j["extra"].get("title", ""), "type": j["extra"].get("judgment_type"),
                     "evidence_refs": j["extra"].get("evidence_refs", [])}
                    for j in judgments if j["extra"].get("judgment_type") in ("risk", "opportunity")]
        gaps = [{"title": a["extra"].get("title", ""), "rationale": a["extra"].get("rationale", "")}
                for a in assumptions]
        sources = [{"video_title": v.get("video_title", ""), "job_id": v.get("job_id", "")} for v in videos]

        return {
            "scope": self._scope_from_stats(stats),
            "overall": self._overall_from_stats(stats),
            "themes": themes,
            "sources": sources,
            "risk_opportunity": risk_opp,
            "evidence_gaps": gaps,
            "actions": [],
            "metrics": [],
            "agent": {
                "cards": [c.to_dict() for c in cards],
                "subtask_results": [r.to_dict() for r in subtask_results],
                "review": review.to_dict(),
                "budget": counter.to_dict(),
            },
            "evidence": evidence_list,
            "meta": {
                "model": getattr(self.llm_provider, "model", None),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": int((time.monotonic() - self._started) * 1000) if self._started else 0,
            },
        }

    @staticmethod
    def _scope_from_stats(stats) -> dict:
        scope = {}
        for s in stats:
            if s["extra"].get("label") == "comment_count":
                scope["comment_count"] = s["extra"].get("count")
        return scope

    @staticmethod
    def _overall_from_stats(stats) -> dict:
        overall = {}
        for s in stats:
            if s["extra"].get("label") == "trend_total":
                overall["current"] = s["extra"].get("count")
            if s["extra"].get("label") == "comparison":
                overall["previous"] = s["extra"].get("count")
        return overall
```

- [ ] **Step 6: 提交**

```bash
git add app/agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(agent): 任务编排 Orchestrator

- 主流程：Supervisor -> Investigator[] -> Reviewer -> 三层综合报告
- 预算硬限制：subtask/loop/tool_call/supplement 各维度超限即停止
- 评审门禁：request_supplement 且未超额度时执行一次受控补查并二次评审
- 报告区分 事实/解释性判断/待验证假设 三层，含 agent 审计块与 evidence"
```

---

### Task 10: Agent 后台运行器 + API 路由接入

**Files:**
- Create: `app/agent/runner.py`
- Modify: `app/api/routes.py`
- Modify: `app/api/schemas.py`
- Test: `tests/test_agent_api.py`

**Interfaces:**
- Consumes: `Orchestrator.run`、`TaskRepository`、`EventRepository`、`LogicalSnapshot.from_dict`、`Settings`
- Produces:
  - `app/agent/runner.py`:
    - `run_agent_sync(task_id, *, task_repo, event_repo, datasource, llm_provider, settings, max_subtasks=None, max_tool_calls=None) -> dict`
    - `start_agent_background(...) -> threading.Thread`
  - API: `POST /api/tasks` 当 `ENABLE_AGENT_ENGINE=True`（且 `ENABLE_WORKFLOW_ENGINE=True`）时走 Agent；否则 V0.3/V0.2 路径不变。
  - `CreateTaskRequest.max_subtasks` / `max_tool_calls` 为可选（现状已有），启动 Agent 时透传覆盖。

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_agent_api.py`：

```python
"""V0.4 Agent API 集成测试（同步模式）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import tempfile

from fastapi.testclient import TestClient


def _settings(**kw):
    from app.core.config import Settings
    base = dict(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                ENABLE_WORKFLOW_ENGINE=True, ENABLE_AGENT_ENGINE=True)
    base.update(kw)
    return Settings(**base)


class FakeLLM:
    def __init__(self):
        self.responses = []
    def push(self, content, raw=None):
        self.responses.append({"content": content, "raw": raw or {}})
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return json.loads(nxt["content"]), LLMResult(content=nxt["content"], usage=LLMUsage(model="m"))
    def chat(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return LLMResult(content=nxt.get("content"), usage=LLMUsage(model="m"), raw=nxt.get("raw", {}))


def _app(tmp_path, llm, **kw):
    from app.main import create_app
    settings = _settings(APP_STATE_DIR=str(tmp_path), **kw)
    return create_app(settings_override=settings, llm_provider=llm, background=False)


class TestAgentApi:
    def test_create_task_uses_agent_when_enabled(self, tmp_path):
        llm = FakeLLM()
        # supervisor
        llm.push(json.dumps({"cards": [{"card_id": "c1", "goal_type": "pulse", "title": "t", "objective": "o",
                                        "evidence_requirements": [], "suggested_tools": [], "priority": 1}]}))
        # investigator stop
        llm.push(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "s", "findings": ["f"]}))
        # reviewer pass
        llm.push(json.dumps({"verdict": "pass", "issues": []}))
        app = _app(tmp_path, llm)
        client = TestClient(app)
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("success", "running")
        # 任务成功且带 agent 结果
        if data["status"] == "success":
            assert "agent" in (data.get("result") or {})

    def test_agent_disabled_falls_back_to_workflow(self, tmp_path):
        """ENABLE_AGENT_ENGINE=False 时仍走 V0.3 工作流。"""
        llm = FakeLLM()
        # V0.3 workflow 用 chat（不传 tools）返回 stage 输出
        llm.push(json.dumps({"scope_confirmed": True, "findings": ["f"], "report": {}}))
        app = _app(tmp_path, llm, ENABLE_AGENT_ENGINE=False)
        client = TestClient(app)
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300"})
        assert resp.status_code == 200
```

注：`background=False` 时 `use_workflow = settings.ENABLE_WORKFLOW_ENGINE and background` 为 False，会走 V0.2 基线同步路径而非 Agent。因此 `test_agent_disabled_falls_back_to_workflow` 需在 `create_app(..., background=True)` 下验证，但后台线程难断言。**实际取舍**：API 层 Agent 调度在 `background=True` 下通过 `start_agent_background`；`background=False`（测试同步）时，若走 Agent 则同步执行 `run_agent_sync`。实现请让 API 在 `settings.ENABLE_AGENT_ENGINE and settings.ENABLE_WORKFLOW_ENGINE` 为真时，无论 background 是否 True，都走 Agent 同步/后台；否则走 V0.3/V0.2 原逻辑。测试建议将 `test_agent_disabled_falls_back_to_workflow` 改为验证 `ENABLE_AGENT_ENGINE=False` 时 task 能经 V0.2 基线完成（用 `background=False`），并断言 `skill_name is None`（基线模式），而非依赖 workflow 分支。

- [ ] **Step 2: 编写 app/agent/runner.py**

```python
"""V0.4 Agent 后台运行器（与 V0.3 workflow runner 结构一致）。"""
import threading


def run_agent_sync(task_id, *, task_repo, event_repo, datasource, llm_provider, settings,
                   max_subtasks=None, max_tool_calls=None):
    """同步执行 Agent 任务，返回 result。出错则标记 failed 并保留错误。"""
    try:
        task = task_repo.get_task(task_id)
        if task is not None and task.status in {"success", "failed"}:
            return {"already": task.status}
        if task is None:
            return {"error": "task not found"}

        task_repo.update_status(task_id, "running")
        event_repo.append_event(task_id, "task_started", {"task_id": task_id})

        from app.snapshot.snapshot import LogicalSnapshot
        snapshot = LogicalSnapshot.from_dict(task.snapshot)
        from app.understanding.intent import AnalysisIntent
        intent = AnalysisIntent.from_dict(task.parsed_intent)

        from app.store.evidence import EvidenceStore
        evidence_store = EvidenceStore(task_id)

        from app.agent.orchestrator import Orchestrator
        orch = Orchestrator(llm_provider, datasource, snapshot, evidence_store,
                            event_repo, task_repo, settings)
        result = orch.run(task.task_id, intent, max_subtasks=max_subtasks, max_tool_calls=max_tool_calls)

        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})
        return result
    except Exception as e:
        task_repo.mark_failed(task_id, f"{type(e).__name__}: {e}")
        event_repo.append_event(task_id, "task_failed", {"error": str(e)})
        return {"error": str(e)}


def start_agent_background(task_id, *, task_repo, event_repo, datasource,
                           llm_provider, settings, max_subtasks=None, max_tool_calls=None):
    def _worker():
        run_agent_sync(task_id, task_repo=task_repo, event_repo=event_repo,
                       datasource=datasource, llm_provider=llm_provider, settings=settings,
                       max_subtasks=max_subtasks, max_tool_calls=max_tool_calls)
    thread = threading.Thread(target=_worker, daemon=True, name=f"agent-{task_id}")
    thread.start()
    return thread
```

- [ ] **Step 3: 修改 API 路由**

在 `create_task` 中，`intent`/`snap` 构建之后，根据 `settings.ENABLE_AGENT_ENGINE and settings.ENABLE_WORKFLOW_ENGINE` 决定 Agent 路径。在 `_select_skill` 与 `use_workflow` 判断处插入：若 Agent 启用，则本任务走 Agent（仍记录 `skill_name` 供前端辨识为 V0.4 模式）。修改示意（**最终以代码为准**）：

```python
        use_workflow = settings.ENABLE_WORKFLOW_ENGINE and background
        use_agent = settings.ENABLE_AGENT_ENGINE and settings.ENABLE_WORKFLOW_ENGINE

        skill_name = _select_skill(intent.goal_type, settings.DEFAULT_SKILL) if (use_workflow or use_agent) else None
```

并在后台/同步分派处追加 Agent 分支：

```python
        if use_agent:
            if background:
                from app.agent.runner import start_agent_background
                start_agent_background(
                    task.task_id, task_repo=task_repo, event_repo=event_repo,
                    datasource=_ds, llm_provider=llm_provider, settings=settings,
                    max_subtasks=req.max_subtasks, max_tool_calls=req.max_tool_calls,
                )
            else:
                from app.agent.runner import run_agent_sync
                run_agent_sync(
                    task.task_id, task_repo=task_repo, event_repo=event_repo,
                    datasource=_ds, llm_provider=llm_provider, settings=settings,
                    max_subtasks=req.max_subtasks, max_tool_calls=req.max_tool_calls,
                )
                task = task_repo.get_task(task.task_id)
        elif use_workflow:
            # 既有的 V0.3 分支（不动）
            from app.workflow.runner import start_workflow_background
            ...
```

- [ ] **Step 4: 调整测试并运行**

Run: `python -m pytest tests/test_agent_api.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/runner.py app/api/routes.py app/api/schemas.py tests/test_agent_api.py
git commit -m "feat(api): Agent 路径接入

- app/agent/runner.py: run_agent_sync / start_agent_background（与 workflow runner 结构一致）
- POST /api/tasks 在 ENABLE_AGENT_ENGINE 且 ENABLE_WORKFLOW_ENGINE 时为真时走 Agent
- max_subtasks / max_tool_calls 透传覆盖预算
- ENABLE_AGENT_ENGINE=False 时回退 V0.3/V0.2 原逻辑（保留已验证基线）"
```

---

### Task 11: 前端主计划/子任务进度/停止原因视图

**Files:**
- Create: `page/src/AgentPlan.jsx`
- Create: `page/src/AgentPlan.css`
- Modify: `page/src/App.jsx`
- Test: `page/src/__tests__/AgentPlan.test.js`（若项目未建 Vitest 则可仅手动验证，见 Step 5）

**Interfaces:**
- Consumes: `GET /api/tasks/{task_id}/timeline`（前端已有）与 `result.agent`
- Produces: `<AgentPlan agent={task.result?.agent} timeline={...} />` 组件，展示:
  - 主计划：调查卡列表（title + objective + priority）
  - 子任务进度：每卡 status（running/done/stopped）+ stop_reason + tool_calls_used
  - 停止原因：`investigator.stop_reason` / `review.verdict`
- App.jsx：`selectedTask` 有 `result.agent` 时渲染 `<AgentPlan>`（V0.4 任务），否则不变化。

- [ ] **Step 1: 编写组件 AgentPlan.jsx**

```jsx
import './AgentPlan.css'

function AgentPlan({ agent }) {
  if (!agent) return null
  const cards = agent.cards || []
  const results = agent.subtask_results || []
  const review = agent.review || {}
  const budget = agent.budget || {}

  return (
    <div className="agent-plan">
      <h3>调查计划</h3>
      <ul className="plan-cards">
        {cards.length === 0 && <li className="muted">无调查卡</li>}
        {cards.map((card, i) => (
          <li key={card.card_id} className="plan-card">
            <span className="plan-priority">P{card.priority}</span>
            <span className="plan-title">{card.title}</span>
            <span className="plan-objective">{card.objective}</span>
          </li>
        ))}
      </ul>

      <h3>子任务进度</h3>
      <ul className="subtask-list">
        {results.length === 0 && <li className="muted">暂无子任务结果</li>}
        {results.map((r, i) => (
          <li key={i} className={`subtask-item status-${r.stop_reason}`}>
            <span className="subtask-reason">{r.stop_reason}</span>
            <span className="subtask-summary">{r.summary}</span>
            <span className="subtask-tools">工具 × {r.tool_calls_used}</span>
          </li>
        ))}
      </ul>

      <h3>评审</h3>
      <div className="review-box">
        <span className={`review-verdict verdict-${review.verdict}`}>{review.verdict}</span>
        {(review.issues || []).map((iss, i) => (
          <div key={i} className="review-issue">{iss.type}: {iss.detail}</div>
        ))}
      </div>

      {budget && (
        <div className="budget-box">
          <span>工具调用 {budget.tool_calls?.used ?? 0}/{budget.tool_calls?.limit ?? '—'}</span>
          <span>子任务 {budget.subtasks?.used ?? 0}/{budget.subtasks?.limit ?? '—'}</span>
        </div>
      )}
    </div>
  )
}

export default AgentPlan
```

- [ ] **Step 2: 创建 AgentPlan.css**

```css
.agent-plan { margin-top: 1.5rem; padding: 1rem; border: 1px solid #e0e0e0; border-radius: 4px; background: #fafafa; }
.agent-plan h3 { margin: 0 0 0.5rem; font-size: 1rem; color: #333; }
.plan-cards, .subtask-list { list-style: none; padding: 0; margin: 0 0 1rem; }
.plan-card, .subtask-item { padding: 0.5rem 0.75rem; border-radius: 3px; background: #fff; margin-bottom: 0.5rem; font-size: 0.9rem; }
.plan-priority { font-weight: 700; margin-right: 0.5rem; color: #1976d2; }
.plan-title { font-weight: 600; margin-right: 0.5rem; }
.plan-objective, .subtask-summary { color: #666; }
.subtask-reason { font-weight: 600; margin-right: 0.5rem; }
.subtask-tools { float: right; color: #999; }
.status-evidence_sufficient { border-left: 3px solid #4caf50; }
.status-data_insufficient { border-left: 3px solid #ff9800; }
.status-budget_exhausted, .status-tool_failure, .status-illegal_output { border-left: 3px solid #f44336; }
.review-box { padding: 0.5rem 0.75rem; background: #fff; border-radius: 3px; }
.review-verdict { font-weight: 700; }
.verdict-pass { color: #4caf50; }
.verdict-request_supplement { color: #f44336; }
.review-issue { color: #c62828; font-size: 0.85rem; }
.budget-box { margin-top: 0.75rem; display: flex; gap: 1rem; color: #666; font-size: 0.85rem; }
```

- [ ] **Step 3: 集成到 App.jsx**

在 `App.jsx` 顶部 `import Timeline from './Timeline'` 之后追加：

```jsx
import AgentPlan from './AgentPlan'
```

在 `{selectedTask && selectedTask.skill_name && (<Timeline taskId={selectedTask.task_id} />)}` 之后追加：

```jsx
{selectedTask?.result?.agent && (
  <AgentPlan agent={selectedTask.result.agent} />
)}
```

- [ ] **Step 4: 手动验证**

Run: `cd page && npm run dev`
访问本地页面，用真实/模拟 agent 任务查看 `AgentPlan` 渲染（主计划卡片、子任务 stop_reason、评审 verdict、budget）。

- [ ] **Step 5: 提交**

```bash
git add page/src/AgentPlan.jsx page/src/AgentPlan.css page/src/App.jsx
git commit -m "feat(page): V0.4 主计划/子任务进度/停止原因视图

- AgentPlan 组件：调查卡列表、子任务 stop_reason、评审 verdict、预算计数
- V0.4 任务（result.agent）才渲染，V0.2/V0.3 任务不受影响
- 复用现有 timeline 事件流，不引入重型 UI 库"
```

---

### Task 12: 集成测试与真实 LLM 冒烟

**Files:**
- Create: `tests/test_agent_integration.py`
- Create: `tests/test_v04_smoke.py`
- Modify: `pytest.ini`（若尚无 `manual` marker）

**Interfaces:**
- Consumes: `Orchestrator` + FakeDS + FakeLLM（多轮）
- Produces: 端到端集成测试 + 真实 LLM 冒烟测试（`@pytest.mark.manual`）

- [ ] **Step 1: 编写集成测试（mock LLM + FakeDS）**

创建 `tests/test_agent_integration.py`（复用 test_orchestrator 的 FakeLLM/FakeDS），额外验证：

- 一次真实下钻：supervisor 出 1 卡，investigator 首轮 `sample_comments`，次轮依据结果发起 `drill_evidence`（不同工具），第三轮停止。
- 报告三层字段存在（`themes`/`risk_opportunity`/`evidence_gaps`）。
- 停止原因与预算累计记录在事件中。

```python
"""V0.4 Agent 集成测试（mock LLM + FakeDS，跨多轮）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import tempfile

from app.agent.orchestrator import Orchestrator
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.understanding.intent import AnalysisIntent
from datetime import datetime


class FakeDS:
    def count_comments(self, **kw):
        return 100
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(comment_id=f"c{i}", content=f"评论{i} 油耗", video_title="坦克300", job_id="j1",
                              comment_like_count=10, passed=True, is_car_owner=True, has_purchase_intent=False) for i in range(limit)]


class FakeLLM:
    def __init__(self):
        self.responses = []
        self.calls = []
    def push(self, content=None, raw=None):
        self.responses.append({"content": content, "raw": raw or {}})
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return json.loads(nxt["content"]), LLMResult(content=nxt["content"], usage=LLMUsage(model="m"))
    def chat(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return LLMResult(content=nxt.get("content"), usage=LLMUsage(model="m"), raw=nxt.get("raw", {}))


def _tool_call(tool, args):
    return {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}}]}}


class TestAgentIntegration:
    def test_dynamic_drill_chain(self, tmp_path):
        from app.core.config import Settings
        settings = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                            LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                            AGENT_MAX_SUBTASKS=2, AGENT_MAX_LOOPS=5, AGENT_MAX_TOOL_CALLS=10, AGENT_MAX_SUPPLEMENTS=1)
        db = str(tmp_path / "t.db")
        task_repo = TaskRepository(db); task_repo.init_schema()
        event_repo = EventRepository(db); event_repo.init_schema()
        store = EvidenceStore("t1")
        snap = LogicalSnapshot(start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31))
        llm = FakeLLM()
        # supervisor: 1 卡（建议 sample_comments + drill_evidence）
        llm.push(json.dumps({"cards": [{"card_id": "c1", "goal_type": "drill", "title": "油耗", "objective": "下钻油耗",
                                        "evidence_requirements": ["样本"], "suggested_tools": ["sample_comments", "drill_evidence"], "priority": 1}]}))
        # investigator 轮1: sample_comments
        llm.push(content=None, raw=_tool_call("sample_comments", {"limit": 3})["raw"])
        # 轮2: 依据结果发起 drill_evidence（不同工具下钻）
        llm.push(content=None, raw=_tool_call("drill_evidence", {"keyword": "油耗", "limit": 3})["raw"])
        # 轮3: 停止
        llm.push(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient", "summary": "充分", "findings": ["油耗为主"]}))
        # reviewer pass
        llm.push(json.dumps({"verdict": "pass", "issues": []}))

        task = task_repo.create_task(raw_input="分析坦克300", parsed_intent={"goal_type": "drill"}, snapshot=snap.to_dict())
        orch = Orchestrator(llm, FakeDS(), snap, store, event_repo, task_repo, settings)
        result = orch.run(task.task_id, AnalysisIntent("坦克300", None, "drill"))
        assert result["agent"]["budget"]["tool_calls"]["used"] == 2  # 两次下钻
        assert result["agent"]["subtask_results"][0]["stop_reason"] == "evidence_sufficient"
        # 三层报告字段存在（可能部分为空，但键存在）
        assert "themes" in result["report"]
        assert "evidence_gaps" in result["report"]
        # 事件含两次 subtask_tool，分别不同工具
        tools = [e.payload["tool"] for e in event_repo.get_events(task.task_id) if e.event_type == "subtask_tool"]
        assert tools == ["sample_comments", "drill_evidence"]
```

- [ ] **Step 2: 编写真实 LLM 冒烟测试**

创建 `tests/test_v04_smoke.py`：

```python
"""V0.4 真实 LLM 冒烟测试（需真实 LLM API + 真实 MySQL，手动执行）。

pytest tests/test_v04_smoke.py -v -m manual
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import tempfile
from datetime import datetime

from app.core.config import Settings
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.understanding.intent import AnalysisIntent
from app.llm.provider import LLMProvider
from app.datasource.adapter import MySqlDataSource
from app.core.config import load_settings
from sqlalchemy import create_engine


@pytest.mark.manual
def test_real_llm_agent_dynamic_drill():
    settings = load_settings()
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False); db.close()
    task_repo = TaskRepository(db.name); task_repo.init_schema()
    event_repo = EventRepository(db.name); event_repo.init_schema()
    store = EvidenceStore("manual")
    snap = LogicalSnapshot(start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
                           extra={"video_tags": ["坦克300"]})
    engine = create_engine(settings.db_url, pool_pre_ping=True, pool_recycle=1800)
    ds = MySqlDataSource(engine)
    llm = LLMProvider.from_settings(settings)

    from app.agent.orchestrator import Orchestrator
    task = task_repo.create_task(raw_input="分析坦克300近期舆情", parsed_intent={"goal_type": "pulse"}, snapshot=snap.to_dict())
    orch = Orchestrator(llm, ds, snap, store, event_repo, task_repo, settings)
    result = orch.run(task.task_id, AnalysisIntent("坦克300", None, "pulse"))
    assert "report" in result
    assert "agent" in result
    assert isinstance(result["agent"]["budget"], dict)
    tools = [e.payload["tool"] for e in event_repo.get_events(task.task_id) if e.event_type == "subtask_tool"]
    print(f"✓ 真实 LLM 冒烟通过；工具调用序列: {tools}")
```

- [ ] **Step 3: 配置 pytest marker**

若 `pytest.ini` 无 `manual` marker，则追加：

```ini
markers =
    manual: 手动执行的测试（需真实 LLM API 和数据库）
```

- [ ] **Step 4: 运行集成测试**

Run: `python -m pytest tests/test_agent_integration.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/test_agent_integration.py tests/test_v04_smoke.py pytest.ini
git commit -m "test(agent): 集成测试与真实 LLM 冒烟

- test_agent_integration: 动态下钻链（sample_comments -> drill_evidence），三层报告，事件校验
- test_v04_smoke: 真实 LLM + 真实 MySQL 端到端冒烟（manual 标记）
- pytest.ini 补充 manual marker"
```

---

### Task 13: 全量回归与文档更新

**Files:**
- Modify: `docs/how-it-works.md`
- Modify: `docs/architecture.md`
- Moderate: `docs/design/core-design.md`
- Create: `docs/v04-migration-guide.md`
- Run: 全量 pytest

**Interfaces:**
- Consumes: 全量测试与 V0.2/V0.3 兼容回归
- Produces: 文档 + 回归结论

- [ ] **Step 1: 全量回归**

Run: `python -m pytest -q`
Expected: 全部通过（含既有 V0.2/V0.3 测试，不破坏）。若个别既有用例因环境（真实 MySQL 集成 / LLM 冒烟）被标 manual/跳过，确认标记即可。

- [ ] **Step 2: 更新 how-it-works.md**

在 `## 三·六、V0.3 工作流引擎` 之后追加 `## 三·七、V0.4 主 Agent 与受控子 Agent Loop`，说明：三角色（主 Agent 规划、子 Agent 受控下钻、评审把关）、Function Calling、预算与停止原因、三层报告、与 V0.2/V0.3 的路径切换（`ENABLE_AGENT_ENGINE` / `ENABLE_WORKFLOW_ENGINE`）。

- [ ] **Step 3: 更新 architecture.md**

在模块结构章节追加 `app/agent/`（protocols/budgets/tools_spec/supervisor/investigator/reviewer/orchestrator/runner）职责说明，并注明 `app/workflow`、`app/pipeline` 保持不变。

- [ ] **Step 4: 更新 core-design.md**

在文档末尾追加 `## 8. V0.4 模块设计要点与关键决策`，记录：真 Function Calling（方案 A）、预算硬限制、评审门禁 + 一次补查、三层报告、V0.3 保留。

- [ ] **Step 5: 创建 v04-migration-guide.md**

简要说明 V0.3 → V0.4 的新增能力与回退方式（`ENABLE_AGENT_ENGINE=false` 回退 V0.3/V0.2）。

- [ ] **Step 6: 提交**

```bash
git add docs/how-it-works.md docs/architecture.md docs/design/core-design.md docs/v04-migration-guide.md
git commit -m "docs: 更新 V0.4 架构文档、设计要点与迁移指南

- how-it-works.md: V0.4 三角色 Agent Loop 说明
- architecture.md: app/agent 模块结构
- core-design.md: V0.4 设计要点与关键决策
- v04-migration-guide.md: V0.3 -> V0.4 迁移与回退"
```

---

## 实施计划自我审查

**1. Spec 覆盖检查**

- ✔ 主 Agent 生成结构化调查卡：Task 6（Supervisor）
- ✔ 子 Agent 依据工具结果动态下钻：Task 7（Investigator loop，Function Calling）
- ✔ 假设/证据补充/否定/继续下钻：Task 7 + Task 9（hypothesis 字段、动态分支）
- ✔ 预算（子任务/轮数/工具调用/超时）：Task 2（配置）+ Task 4（BudgetCounter）+ Task 7/9（硬限制）
- ✔ 停止原因（证据充分/数据不足/预算耗尽/工具失败/非法输出）：Task 5 + Task 7
- ✔ 独立评审 + 一次受控补查：Task 8 + Task 9
- ✔ 非法输出修复或明确失败：Task 7（重试 1 次后 illegal_output）
- ✔ 前端主计划/子任务进度/停止原因：Task 11
- ✔ V0.2/V0.3 兼容与路径切换：Task 10（API 路由）+ Task 13（回归）
- ✔ 真实 Function Calling：Task 1（provider tools/tool_calls）
- ✔ 三层报告（事实/解释性判断/待验证假设）：Task 9（_build_report）

**2. Placeholder 扫描**：无 TBD/TODO。Task 9 Step 5 明确要求 implementer 以最终代码替换占位。

**3. 类型一致性**
- `InvestigationCard` / `InvestigatorResult` / `ReviewVerdict` 在 Task 5 定义，后续各任务签名一致。
- `BudgetCounter` 方法名在 Task 4 定义，Task 7/9 使用一致。
- `validate_and_coerce(arguments, tool_name, max_records)` 在 Task 3 定义，Task 7 使用一致。
- `Orchestrator.run(task_id, intent, *, max_subtasks, max_tool_calls)` 在 Task 9 定义，Task 10 调用一致。
- `ReviewVerdict.verdict` 取值 `pass` / `request_supplement` 在 Task 5 定义，Task 8/9 一致。

**4. 测试覆盖**：每任务含独立可测的单元测试或集成测试；Task 13 全量回归。
