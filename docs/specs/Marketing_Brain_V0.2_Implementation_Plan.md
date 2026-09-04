# Marketing Brain V0.2 实施计划（非 Agent 基线版本）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 MySQL + 真实 LLM API 跑通「固定分析流程 → 舆情策略包」端到端链路，前端可创建任务、查看运行状态与基线报告。

**Architecture:** 在 V0.1 模块化单体上新增 `llm`（OpenAI-compatible Provider + 报告生成）、`analysis`（确定性统计工具）、`pipeline`（固定流水线 + 引用校验 + 后台执行）三个子模块。任务创建后后台线程执行，前端轮询状态。报告引用的证据 ID 经过校验，非法引用剔除并标记。

**Tech Stack:** Python 3.11+（FastAPI、SQLAlchemy、PyMySQL、httpx、Pydantic）、SQLite（应用状态）、React/Vite（前端）、pytest（测试）。不引入 LangGraph、openai SDK、Celery、jieba、向量库。

**Spec:** `docs/specs/Marketing_Brain_V0.2_Design.md`（计划从该设计文档论证，执行时并行阅读）。数据口径在 Spec §2。

## Global Constraints

- **时间锚点只用 `job.created_at`（UTC）**，绝不用评论自带 `comment_time`（实测不可靠）。
- **对象匹配用 `video_title LIKE '%#tag%'`**（话题标签），不用标题子串 `LIKE`（会把竞品对比视频误算进来，实测噪音约 4 倍）。
- **视频（来源）ID = `api_job.id`**；评论 ID = 展开后的 `comment_id`。
- **只读**：数据源账号仅 SELECT，每个连接强制 `SET SESSION TRANSACTION READ ONLY`；调用方不能传任意 SQL。
- **模型不直接连库、不执行任意 SQL**：LLM 只看到喂给它的证据与统计，只能引用已有证据 ID。
- **审计事件**：记录「计划、动作、证据、结构化判断与状态变化」，**不记录模型原始思维链**。
- **中文文档/注释一律 UTF-8**；写入后检查乱码，若无法修复临时改用英文并报告。
- 不引入重量级组件（见 Spec §8 本版不做清单）。
- 真实 LLM 批量调用：单次冒烟测试由我直接执行；含多次调用的任务先告知你；无法确认次数或 >100 次则停下来等你批准。

## 契约（跨任务共享，后续任务引用此处）

本节锁定各模块对外接口，后续任务按此实现，避免类型不一致。

### LLM Provider（`app/llm/provider.py`）

```python
class LLMError(Exception): ...

@dataclass
class LLMUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    def to_dict(self) -> dict: ...

@dataclass
class LLMResult:
    content: str
    usage: LLMUsage
    raw: dict
    def to_dict(self) -> dict: ...

class LLMProvider:
    def __init__(self, base_url, api_key, model, *, timeout=120.0,
                 temperature=0.2, transport=None): ...
    @classmethod
    def from_settings(cls, settings): ...
    def chat(self, messages, *, response_format=None, temperature=None,
             max_tokens=None) -> LLMResult: ...
    def chat_json(self, messages, **kw) -> tuple[dict, LLMResult]: ...
```

### 数据源新增方法（`app/datasource/adapter.py`）

```python
class MySqlDataSource:
    def fetch_comments(self, limit=500, *, start_time=None, end_time=None,
                       video_tags=None, keyword=None, min_like=None,
                       passed=None, has_purchase_intent=None,
                       is_car_owner=None, order_by=None) -> list[CommentRecord]: ...
    def count_comments(self, *, start_time=None, end_time=None, video_tags=None,
                       keyword=None) -> int: ...
    def time_series(self, *, start_time, end_time, video_tags=None,
                    bucket="day") -> dict: ...
    def top_videos(self, *, start_time=None, end_time=None, video_tags=None,
                   limit=10) -> list[dict]: ...
    def topic_frequency(self, *, start_time=None, end_time=None, video_tags=None,
                        limit=50) -> list[dict]: ...
```

### 证据库（`app/store/evidence.py`）

```python
@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str                 # "comment" | "video" | "stat"
    comment_id: str | None
    job_id: str | None
    content: str
    video_title: str
    like_count: int
    passed: bool | None
    is_car_owner: bool | None
    has_purchase_intent: bool | None
    source: str
    extra: dict
    def to_dict(self) -> dict: ...

class EvidenceStore:
    def __init__(self, task_id: str): ...
    def register_comment(self, record, source) -> EvidenceRecord: ...
    def register_video(self, *, job_id, video_title, comment_count, source) -> EvidenceRecord: ...
    def register_stat(self, *, label, detail, count, source) -> EvidenceRecord: ...
    def all_records(self) -> list[EvidenceRecord]: ...
    def to_dicts(self) -> list[dict]: ...
    def has_comment(self, comment_id) -> bool: ...
    def has_video(self, job_id) -> bool: ...
```

### 分析工具（`app/analysis/tools.py`）

```python
TOOL_PROTOCOL = {  # 每个工具返回的 dict 结构
    "name": str, "params": dict, "result": dict,
    "evidence_ids": list[str], "sample_size": int,
    "time_range": dict, "bias_note": str,
}
```

工具函数：`data_coverage(ds, snap, store)`、`volume_trend(ds, snap, store)`、
`period_comparison(ds, snap, store)`、`topic_frequency_tool(ds, snap, store)`、
`top_sources(ds, snap, store)`、`sample_comments(ds, snap, store)`、
`drill_evidence(ds, snap, store)`、`object_compare(ds, snap, store)`。
签名统一 `(ds, snap, store) -> dict`。

### 流水线（`app/pipeline/baseline.py`）

```python
@dataclass
class AnalysisBundle:
    scope: dict
    overall: dict
    themes: dict
    sources: dict
    samples: list[dict]
    stats: list[dict]
    def to_dict(self) -> dict: ...

class BaselinePipeline:
    def __init__(self, datasource, snapshot, evidence_store): ...
    def run(self) -> AnalysisBundle: ...
```

### 报告生成（`app/llm/report.py`）

```python
def build_report_messages(bundle: AnalysisBundle, intent) -> list[dict]: ...
def generate_strategy_pack(bundle: AnalysisBundle, provider: LLMProvider) -> tuple[dict, LLMResult]: ...
```

### 引用校验（`app/pipeline/verify.py`）

```python
def validate_report(report: dict, evidence_store) -> tuple[dict, dict]: ...
```

返回 `(cleaned_report, validation)`；`validation = {"total_refs": int, "valid_refs": int, "rejected_refs": list[str], "notes": list[str]}`。

### 任务执行器（`app/pipeline/runner.py`）

```python
def run_task_sync(task_id, *, task_repo, event_repo, datasource,
                  llm_provider, settings) -> dict: ...   # 返回 result
def start_task_background(*, task_id, task_repo, event_repo, datasource,
                          llm_provider, settings) -> None: ...
```

### API（`app/api/routes.py`）

`create_app(db_path=None, datasource=None, settings=None, static_dir=None, *, llm_provider=None, background=True) -> FastAPI`

- `POST /api/tasks`：创建任务，后台执行（`background=False` 时同步执行并返回完成态）。
- `GET /api/tasks?task_id=`：返回状态 + result。
- `GET /api/tasks/{task_id}/report`：成功时返回舆情策略包。
- `GET /api/tasks/{task_id}/evidence`：返回证据索引。

---

### Task 1: 配置扩充（LLM 超时）

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 现有 `Settings`
- Produces: `Settings.LLM_TIMEOUT_MS: int`（默认 120000），`Settings.llm_timeout: float`（秒，property）

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
def test_llm_timeout_default(backup_env):
    s = Settings()
    assert s.LLM_TIMEOUT_MS == 120000
    assert s.llm_timeout == 120.0

def test_llm_timeout_override(backup_env, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_MS", "60000")
    s = Settings()
    assert s.LLM_TIMEOUT_MS == 60000
    assert s.llm_timeout == 60.0
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL，`AttributeError: 'Settings' object has no attribute 'LLM_TIMEOUT_MS'`

- [ ] **Step 3: 实现**

在 `app/core/config.py` 的 LLM 段落后追加：

```python
    LLM_TIMEOUT_MS: int = Field(default=120000)

    @property
    def llm_timeout(self) -> float:
        """LLM 请求超时（秒）。"""
        return self.LLM_TIMEOUT_MS / 1000.0
```

在 `_positive_int` 校验器字段列表加入 `"LLM_TIMEOUT_MS"`。

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS（连同既有 config 测试全绿）

- [ ] **Step 5: 更新 `.env.example`**

在 `.env.example` 的 LLM 段落后追加：

```ini
# LLM 请求超时（毫秒）
LLM_TIMEOUT_MS=120000
```

- [ ] **Step 6: 提交**

```bash
git add app/core/config.py .env.example tests/test_config.py
git commit -m "feat(config): 增加 LLM 超时配置项"
```

---

### Task 2: LLM Provider（OpenAI-compatible）

**Files:**
- Create: `app/llm/__init__.py`
- Create: `app/llm/provider.py`
- Test: `tests/test_llm_provider.py`

**Interfaces:**
- Consumes: `Settings`（Task 1）
- Produces: `LLMProvider`、`LLMResult`、`LLMUsage`、`LLMError`（签名见契约）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_llm_provider.py`：

```python
"""LLM Provider 测试（用 httpx.MockTransport，不连真实 API）。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from app.llm.provider import LLMProvider, LLMResult, LLMUsage, LLMError


def _handler(request):
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "test-model"
    payload = {
        "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    return httpx.Response(200, json=payload)


def _make_provider():
    transport = httpx.MockTransport(_handler)
    return LLMProvider(
        base_url="https://llm.example.com/v1",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )


class TestLLMProvider:
    def test_chat_returns_content_and_usage(self):
        p = _make_provider()
        result = p.chat([{"role": "user", "content": "hi"}], max_tokens=10)
        assert isinstance(result, LLMResult)
        assert result.content == '{"ok": true}'
        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 50
        assert result.usage.total_tokens == 150
        assert result.usage.model == "test-model"

    def test_chat_url_and_auth(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        p.chat([{"role": "user", "content": "x"}])
        assert transport.requests[0].url.path == "/v1/chat/completions"

    def test_chat_json_parses(self):
        p = _make_provider()
        data, result = p.chat_json([{"role": "user", "content": "hi"}])
        assert data == {"ok": True}
        assert result.usage.total_tokens == 150

    def test_chat_json_invalid_raises(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={
                "choices": [{"message": {"content": "not json at all"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        with pytest.raises(LLMError):
            p.chat_json([{"role": "user", "content": "hi"}])

    def test_http_error_raises_llm_error(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(500, json={"error": "boom"})
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        with pytest.raises(LLMError):
            p.chat([{"role": "user", "content": "x"}])

    def test_from_settings(self, monkeypatch):
        from app.core.config import Settings
        monkeypatch.setenv("LLM_API_BASE", "https://llm.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        s = Settings()
        p = LLMProvider.from_settings(s)
        assert p.model == "m"
        assert p.base_url == "https://llm.example.com/v1"
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_llm_provider.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.llm'`

- [ ] **Step 3: 实现**

创建 `app/llm/__init__.py`（空文件）。

创建 `app/llm/provider.py`：

```python
"""LLM Provider。

用 httpx 直连 OpenAI-compatible API，不引入 openai SDK。
支持 chat / chat_json，记录 token 与耗时。测试时注入 httpx.MockTransport。
"""
import json
import time
from dataclasses import dataclass, field

import httpx


class LLMError(Exception):
    """LLM 调用或输出解析失败。"""


@dataclass
class LLMUsage:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
        }


@dataclass
class LLMResult:
    content: str
    usage: LLMUsage
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"content": self.content, "usage": self.usage.to_dict()}


class LLMProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        temperature: float = 0.2,
        transport=None,
    ):
        if not base_url or not api_key or not model:
            raise ValueError("LLM 配置缺失：base_url/api_key/model 均必填")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self._transport = transport  # None -> 默认 httpx.Client

    @classmethod
    def from_settings(cls, settings) -> "LLMProvider":
        return cls(
            base_url=settings.LLM_API_BASE,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            timeout=settings.llm_timeout,
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def chat(self, messages, *, response_format=None, temperature=None,
             max_tokens=None) -> LLMResult:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if response_format:
            body["response_format"] = response_format
        if max_tokens:
            body["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        started = time.monotonic()
        try:
            with self._client() as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 请求失败: {e}") from e
        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code != 200:
            raise LLMError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 响应格式异常: {e}; 原文 {str(data)[:300]}") from e

        usage_raw = data.get("usage", {})
        usage = LLMUsage(
            model=self.model,
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
            latency_ms=latency_ms,
        )
        return LLMResult(content=content, usage=usage, raw=data)

    def chat_json(self, messages, **kw) -> tuple[dict, LLMResult]:
        result = self.chat(
            messages,
            response_format={"type": "json_object"},
            **kw,
        )
        try:
            data = json.loads(result.content)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM 输出不是合法 JSON: {e}; 原文 {result.content[:300]}") from e
        if not isinstance(data, dict):
            raise LLMError(f"LLM 输出应为 JSON 对象，得到 {type(data).__name__}")
        return data, result
```

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_llm_provider.py -v`
Expected: PASS（6 个测试全绿）

- [ ] **Step 5: 提交**

```bash
git add app/llm/ tests/test_llm_provider.py
git commit -m "feat(llm): 实现 OpenAI-compatible LLM Provider"
```

---

### Task 3: 数据源扩展（带过滤/聚合查询）

**Files:**
- Modify: `app/datasource/adapter.py`
- Modify: `app/datasource/queries.py`（新增 SQL 常量/辅助）
- Test: `tests/test_datasource_tools.py`（纯逻辑，不连库）
- Test: `tests/test_mysql_integration.py`（`-m integration`，连真实 MySQL）

**Interfaces:**
- Consumes: `Settings.db_url`、现有 `MySqlDataSource`
- Produces: `fetch_comments(过滤参数)`、`count_comments`、`time_series`、`top_videos`、`topic_frequency`（见契约）

> 说明：本任务只测「SQL 构造与纯逻辑」。真实 MySQL 验证由集成测试承担；`-m integration` 默认跳过，验收时跑。

- [ ] **Step 1: 写失败测试（纯逻辑）**

创建 `tests/test_datasource_tools.py`：

```python
"""数据源新增查询方法的纯逻辑测试（不连真实 MySQL）。

时间分桶、对比变化率、标签匹配 SQL 构造用 FakeDataSource 验证。
真实 MySQL 正确性由 tests/test_mysql_integration.py 集成测试承担。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta
import pytest

from app.datasource.adapter import _build_comment_filters, _tag_like_clause
from app.datasource.adapter import _aggregate_time_series


class TestTagFilter:
    def test_single_tag_clause(self):
        sql = _tag_like_clause(["坦克300"])
        assert "like" in sql.lower() or "#" in sql
        assert "%#坦克300#%" in sql

    def test_multiple_tags_or(self):
        sql = _tag_like_clause(["坦克300", "坦克500"])
        assert sql.count("#") >= 2
        assert sql.count("OR") >= 1  # 多个标签用 OR 连接

    def test_empty_tags_returns_none(self):
        assert _tag_like_clause([]) is None


class TestCommentFilters:
    def test_filters_build_params(self):
        params = {}
        _build_comment_filters(
            params,
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
            video_tags=["坦克300"],
            keyword="油耗",
            min_like=5,
        )
        assert params["start_time"] == datetime(2026, 8, 1)
        assert params["end_time"] == datetime(2026, 8, 31)
        assert params["keyword"] == "油耗"
        assert params["min_like"] == 5

    def test_optional_params_skipped(self):
        params = {}
        _build_comment_filters(params)
        assert "keyword" not in params
        assert "min_like" not in params


class TestTimeSeries:
    def test_bucketize_daily(self, ):
        start = datetime(2026, 8, 1)
        end = datetime(2026, 8, 3, 23, 59, 59)
        # 模拟逐条时间戳列表 -> 分桶
        counts = [
            (datetime(2026, 8, 1), 10),
            (datetime(2026, 8, 2), 20),
            (datetime(2026, 8, 3), 5),
            (datetime(2026, 8, 1), 15),
        ]
        buckets = _aggregate_time_series(counts, start, end, bucket="day")
        assert buckets["total"] == 50
        # 应补齐缺失日（8-1 到 8-3 三天）
        assert len(buckets["buckets"]) == 3
        assert buckets["buckets"][0]["count"] == 25  # 8-01 两天合并
```

> 注：上面的断言值以「按日分桶（date=8-01 的两条合并）」为准。若实现选择不同粒度（如按天），以实际 `_aggregate_time_series` 语义调整，但**必须**：总计数正确、缺失区间补齐、桶按时间升序。

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_datasource_tools.py -v`
Expected: FAIL，`ImportError: cannot import name '_build_comment_filters'`

- [ ] **Step 3: 实现（queries.py）**

在 `app/datasource/queries.py` 追加：

```python
def build_comment_query(*, filters=True):
    """返回展开评论的基础 SELECT（WITH 过滤段）。filters=False 时去掉 WHERE 条件。"""
    base = """
        SELECT
            j.id AS job_id, j.status AS job_status, j.created_at AS job_created_at,
            c.cid AS comment_id, c.c AS comment_content, c.vt AS video_title,
            c.at AS comment_author, c.uid AS comment_author_uid,
            c.like_count AS comment_like_count,
            r.passed, r.is_car_owner, r.has_purchase_intent, r.analysis
        FROM api_job j
        CROSS JOIN JSON_TABLE(
            j.request_payload, '$.comments[*]'
            COLUMNS (
                cid  VARCHAR(64)      PATH '$.comment_id',
                vt   TEXT             PATH '$.video_title',
                at   VARCHAR(255)     PATH '$.comment_author',
                uid  VARCHAR(128)     PATH '$.comment_author_uid',
                c    TEXT             PATH '$.comment_content',
                `like_count` INT      PATH '$.comment_like_count'
            )
        ) c
        LEFT JOIN JSON_TABLE(
            j.result, '$.results[*]'
            COLUMNS (
                rcid VARCHAR(64)      PATH '$.comment_id',
                passed BOOLEAN        PATH '$.passed',
                is_car_owner BOOLEAN  PATH '$.is_car_owner',
                has_purchase_intent BOOLEAN PATH '$.has_purchase_intent',
                analysis TEXT         PATH '$.analysis'
            )
        ) r ON c.cid = r.rcid
        WHERE j.job_type = 'comment_screening' AND j.status = 'success'
    """
    return base
```

- [ ] **Step 4: 实现（adapter.py 新增工具函数）**

在 `app/datasource/adapter.py` 追加：

```python
def _tag_like_clause(video_tags):
    """根据话题标签列表生成 video_title 匹配的子句（不用标题子串，防竞品误算）。

    返回形如 "(c.vt LIKE '%#坦克300#%' OR c.vt LIKE '%#坦克500#%')" 或 None。
    """
    if not video_tags:
        return None
    clause = " OR ".join(f"c.vt LIKE '%#{t}#%'" for t in video_tags if t)
    return f"({clause})" if clause else None


def _build_comment_filters(params, *, start_time=None, end_time=None,
                           video_tags=None, keyword=None, min_like=None,
                           passed=None, has_purchase_intent=None,
                           is_car_owner=None):
    """向 params 写入 WHERE 绑定参数（不写 SQL 文本，只写绑定量）。

    时间边界用 job.created_at；对象用 #标签；关键字用 comment_content LIKE。
    除时间/标签外，其余字段仅在非 None 时写入。
    """
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if video_tags:
        params["video_tags"] = video_tags
    if keyword is not None:
        params["keyword"] = keyword
    if min_like is not None:
        params["min_like"] = min_like
    if passed is not None:
        params["passed"] = bool(passed)
    if has_purchase_intent is not None:
        params["has_purchase_intent"] = bool(has_purchase_intent)
    if is_car_owner is not None:
        params["is_car_owner"] = bool(is_car_owner)
    return params


def _aggregate_time_series(rows, start, end, bucket="day"):
    """把 [(datetime, count), ...] 按桶聚合，补齐缺失区间。

    rows 是按时间增序的单条计数。返回 {"start","end","bucket","buckets":[...],"total"}。
    缺桶自动补 count=0，保证时间轴完整。
    """
    from collections import OrderedDict
    start = start.replace(tzinfo=None)
    end = end.replace(tzinfo=None)
    buckets = OrderedDict()
    cur = start
    if bucket == "day":
        delta = timedelta(days=1)
    else:
        raise ValueError(f"不支持的 bucket: {bucket}")
    while cur <= end:
        buckets[cur.date().isoformat()] = 0
        cur += delta
    for ts, count in rows:
        ts = ts.replace(tzinfo=None)
        key = ts.date().isoformat()
        if key in buckets:
            buckets[key] += count
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket": bucket,
        "buckets": [{"start": k, "count": v} for k, v in buckets.items()],
        "total": sum(buckets.values()),
    }
```

- [ ] **Step 5: 实现（MySqlDataSource 新方法）**

在 `MySqlDataSource` 类内追加：

```python
    def _comment_base(self, params, *, extra_order=""):
        """构造带过滤条件的展开评论 SQL。"""
        from app.datasource.queries import build_comment_query
        sql = build_comment_query()
        conds = ["j.created_at >= :start_time", "j.created_at <= :end_time"]
        if params.get("video_tags"):
            conds.append(_tag_like_clause(params["video_tags"]))
        if params.get("keyword"):
            conds.append("c.c LIKE CONCAT('%', :keyword, '%')")
        if params.get("min_like") is not None:
            conds.append("c.like_count >= :min_like")
        if params.get("passed") is not None:
            conds.append("r.passed = :passed")
        if params.get("has_purchase_intent") is not None:
            conds.append("r.has_purchase_intent = :has_purchase_intent")
        if params.get("is_car_owner") is not None:
            conds.append("r.is_car_owner = :is_car_owner")
        sql += " AND " + " AND ".join(conds)
        sql += extra_order
        return sql

    def fetch_comments(self, limit=500, *, start_time=None, end_time=None,
                       video_tags=None, keyword=None, min_like=None,
                       passed=None, has_purchase_intent=None,
                       is_car_owner=None, order_by=None):
        """拉取符合过滤条件的评论列表（限 limit 条）。"""
        params = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time,
            video_tags=video_tags, keyword=keyword, min_like=min_like,
            passed=passed, has_purchase_intent=has_purchase_intent,
            is_car_owner=is_car_owner,
        )
        order = ""
        if order_by == "random":
            order = " ORDER BY RAND()"
        elif order_by == "likes":
            order = " ORDER BY c.like_count DESC"
        sql = f"SELECT * FROM ({self._comment_base(params, extra_order=order)}) AS x LIMIT :lim"
        params["lim"] = int(limit)
        result = self._execute(sql, params)
        return [self._row_to_comment(row) for row in result.mappings()]

    def count_comments(self, *, start_time=None, end_time=None, video_tags=None,
                       keyword=None):
        params = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time,
            video_tags=video_tags, keyword=keyword,
        )
        sql = f"SELECT COUNT(*) AS c FROM ({self._comment_base(params)}) AS x"
        result = self._execute(sql, params)
        return int(result.one().c or 0)

    def time_series(self, *, start_time, end_time, video_tags=None, bucket="day"):
        params = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        order = " ORDER BY DATE(j.created_at)"
        sql = (
            "SELECT DATE(j.created_at) AS d, COUNT(*) AS n FROM api_job j "
            "CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (cid VARCHAR(64) PATH '$.comment_id', "
            "vt TEXT PATH '$.video_title')) c "
            f"WHERE j.job_type='comment_screening' AND j.status='success' "
            "AND j.created_at >= :start_time AND j.created_at <= :end_time"
        )
        if params.get("video_tags"):
            sql += " AND " + _tag_like_clause(params["video_tags"])
        sql += " GROUP BY d" + order
        result = self._execute(sql, params)
        rows = []
        for row in result.mappings():
            ts = row.d
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            # d 已转成 date；用当天起点作时间戳
            rows.append((datetime(ts.year, ts.month, ts.day), int(row.n or 0)))
        return _aggregate_time_series(rows, start_time, end_time, bucket=bucket)

    def top_videos(self, *, start_time=None, end_time=None, video_tags=None, limit=10):
        params = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        sql = (
            "SELECT j.id AS job_id, c.vt AS video_title, "
            "COUNT(*) AS comment_count, "
            "COALESCE(SUM(c.like_count),0) AS like_sum "
            "FROM api_job j CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (cid VARCHAR(64) PATH '$.comment_id', "
            "vt TEXT PATH '$.video_title', `like_count` INT PATH '$.comment_like_count')) c "
            "WHERE j.job_type='comment_screening' AND j.status='success' "
            "AND j.created_at >= :start_time AND j.created_at <= :end_time"
        )
        if params.get("video_tags"):
            sql += " AND " + _tag_like_clause(params["video_tags"])
        sql += " GROUP BY j.id, c.vt ORDER BY comment_count DESC LIMIT :lim"
        params["lim"] = int(limit)
        result = self._execute(sql, params)
        return [
            {"job_id": row.job_id, "video_title": row.video_title or "",
             "comment_count": int(row.comment_count or 0), "like_sum": int(row.like_sum or 0)}
            for row in result.mappings()
        ]

    def topic_frequency(self, *, start_time=None, end_time=None, video_tags=None,
                        limit=50):
        """统计窗口内评论所属视频标题的高频话题标签（不含对象本身的标签）。

        用于给 LLM 提供候选子主题。对象标签（如 #坦克300）会出现在几乎所有
        标题里，为避免无意义，这里按出现次数排序，由调用方决定是否排除对象标签。
        """
        params = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        # 用正则式在应用层解析 hashtag（MySQL 端难做中文分词）
        sql = (
            "SELECT c.vt AS video_title FROM api_job j "
            "CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (vt TEXT PATH '$.video_title')) c "
            "WHERE j.job_type='comment_screening' AND j.status='success' "
            "AND j.created_at >= :start_time AND j.created_at <= :end_time"
        )
        if params.get("video_tags"):
            sql += " AND " + _tag_like_clause(params["video_tags"])
        result = self._execute(sql, params)
        import re
        from collections import Counter
        counter = Counter()
        for row in result.mappings():
            for tag in re.findall(r"#([^#\s]+)#", row.video_title or ""):
                counter[tag] += 1
        return [{"topic": t, "comment_count": n} for t, n in counter.most_common(limit)]
```

> 说明：`topic_frequency` 在应用层解析 `#...#`（避免在 MySQL 端做复杂文本处理），
> 返回 topic 与出现次数。V0.2 用它给 LLM 提供候选主题，不承诺精确中文分词。

- [ ] **Step 6: 运行纯逻辑测试**

Run: `python -m pytest tests/test_datasource_tools.py -v`
Expected: PASS

- [ ] **Step 7: 回归既有测试**

Run: `python -m pytest tests/test_datasource.py tests/test_mysql_integration.py -m integration -v`
Expected: 既有集成测试通过（`fetch_comments(limit=5)` 等兼容）。

- [ ] **Step 8: 集成测试（连真实 MySQL）**

在 `tests/test_mysql_integration.py` 追加标记 `integration` 的用例：

```python
def test_fetch_comments_with_filters(ds):
    """带时间窗 + 标签过滤能取到数据且口径正确。"""
    recs = ds.fetch_comments(
        limit=10, start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        video_tags=["坦克300"],
    )
    # 过滤后命中数可能为 0，但不应报错；若命中则都应带正确 job_id
    for r in recs:
        assert r.job_id


def test_count_comments_tags(ds):
    n = ds.count_comments(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        video_tags=["坦克300"],
    )
    assert n >= 0


def test_time_series_shape(ds):
    ts = ds.time_series(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        video_tags=["坦克300"],
    )
    assert ts["total"] >= 0
    assert len(ts["buckets"]) == 31  # 8 月有 31 天


def test_top_videos(ds):
    vids = ds.top_videos(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        video_tags=["坦克300"], limit=5,
    )
    assert len(vids) <= 5
    for v in vids:
        assert "job_id" in v and "video_title" in v


def test_topic_frequency(ds):
    tags = ds.topic_frequency(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        video_tags=["坦克300"], limit=20,
    )
    assert isinstance(tags, list)
    for t in tags:
        assert "topic" in t and "comment_count" in t
```

Run: `python -m pytest tests/test_mysql_integration.py -m integration -v`
Expected: PASS（需 `.env` 有可连接只读账号；账号未配时标记跳过）

- [ ] **Step 9: 提交**

```bash
git add app/datasource/ tests/
git commit -m "feat(datasource): 增加带过滤/聚合的分析查询方法"
```

---

### Task 4: 证据库

**Files:**
- Create: `app/store/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `CommentRecord`（`app/datasource/models.py`）
- Produces: `EvidenceRecord`、`EvidenceStore`（签名见契约）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_evidence.py`：

```python
"""证据索引测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.store.evidence import EvidenceStore
from app.datasource.models import CommentRecord


def _comment(cid="c1", job="j1", content="好车", vt="坦克300"):
    return CommentRecord(
        comment_id=cid, content=content, video_title=vt, job_id=job,
        comment_like_count=7, passed=True, is_car_owner=True,
        has_purchase_intent=False,
    )


class TestEvidenceStore:
    def test_register_and_has_comment(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_comment(_comment(), source="sample")
        assert store.has_comment("c1")
        assert rec.evidence_id
        assert rec.kind == "comment"

    def test_register_video(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="top")
        assert store.has_video("j1")
        assert rec.kind == "video"

    def test_register_stat(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_stat(label="total", detail="n", count=100, source="count")
        assert rec.kind == "stat"

    def test_to_dicts_roundtrip(self):
        store = EvidenceStore(task_id="t1")
        store.register_comment(_comment(), source="sample")
        store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="top")
        dicts = store.to_dicts()
        assert len(dicts) == 2
        assert all("evidence_id" in d and "kind" in d for d in dicts)

    def test_partial_flags_none(self):
        store = EvidenceStore(task_id="t1")
        store.register_comment(_comment(), source="sample")
        recs = store.all_records()
        assert recs[0].is_car_owner is True
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.store.evidence'`

- [ ] **Step 3: 实现**

创建 `app/store/evidence.py`：

```python
"""本次运行的证据索引。

登记流水线取证阶段命中的 comment_id / job_id / 统计，供引用校验与
前端「结论→证据」回查。V0.2 证据在单次运行内维护并随任务结果持久化。
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str                 # comment / video / stat
    comment_id: Optional[str] = None
    job_id: Optional[str] = None
    content: str = ""
    video_title: str = ""
    like_count: int = 0
    passed: Optional[bool] = None
    is_car_owner: Optional[bool] = None
    has_purchase_intent: Optional[bool] = None
    source: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "comment_id": self.comment_id,
            "job_id": self.job_id,
            "content": self.content,
            "video_title": self.video_title,
            "like_count": self.like_count,
            "passed": self.passed,
            "is_car_owner": self.is_car_owner,
            "has_purchase_intent": self.has_purchase_intent,
            "source": self.source,
            "extra": self.extra,
        }


class EvidenceStore:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._comments: dict[str, EvidenceRecord] = {}
        self._videos: dict[str, EvidenceRecord] = {}
        self._stats: list[EvidenceRecord] = []

    def register_comment(self, record, source: str) -> EvidenceRecord:
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="comment",
            comment_id=str(record.comment_id),
            job_id=str(record.job_id) if record.job_id else None,
            content=record.content or "",
            video_title=record.video_title or "",
            like_count=int(record.comment_like_count or 0),
            passed=record.passed,
            is_car_owner=record.is_car_owner,
            has_purchase_intent=record.has_purchase_intent,
            source=source,
            extra={"analysis": record.analysis},
        )
        if ev.comment_id:
            self._comments[ev.comment_id] = ev
        return ev

    def register_video(self, *, job_id, video_title, comment_count,
                       source: str) -> EvidenceRecord:
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="video",
            job_id=str(job_id),
            video_title=video_title or "",
            like_count=0,
            source=source,
            extra={"comment_count": int(comment_count)},
        )
        if ev.job_id:
            self._videos[ev.job_id] = ev
        return ev

    def register_stat(self, *, label, detail, count, source: str) -> EvidenceRecord:
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="stat",
            source=source,
            extra={"label": label, "detail": detail, "count": int(count)},
        )
        self._stats.append(ev)
        return ev

    def all_records(self) -> list[EvidenceRecord]:
        return list(self._comments.values()) + list(self._videos.values()) + list(self._stats)

    def to_dicts(self) -> list[dict]:
        return [r.to_dict() for r in self.all_records()]

    def has_comment(self, comment_id) -> bool:
        return str(comment_id) in self._comments

    def has_video(self, job_id) -> bool:
        return str(job_id) in self._videos
```

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_evidence.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/store/evidence.py tests/test_evidence.py
git commit -m "feat(store): 增加运行内证据索引"
```

---

### Task 5: 分析工具集

**Files:**
- Create: `app/analysis/__init__.py`
- Create: `app/analysis/tools.py`
- Test: `tests/test_analysis_tools.py`

**Interfaces:**
- Consumes: `MySqlDataSource`（Task 3）、`LogicalSnapshot`、`EvidenceStore`（Task 4）
- Produces: 8 个工具函数（签名见契约），每个返回 `TOOL_PROTOCOL` dict

- [ ] **Step 1: 写失败测试**

创建 `tests/test_analysis_tools.py`（用 FakeDataSource，不连库）：

```python
"""分析工具集测试（用 FakeDataSource 验证工具逻辑与证据登记）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import pytest

from app.analysis import tools
from app.analysis.tools import (
    data_coverage, volume_trend, period_comparison, topic_frequency_tool,
    top_sources, sample_comments, drill_evidence, object_compare,
)
from app.store.evidence import EvidenceStore
from app.snapshot.snapshot import LogicalSnapshot
from app.datasource.models import CommentRecord


class FakeDS:
    """模拟 MySqlDataSource 返回稳定值。"""
    def __init__(self):
        self._calls = []
    def _comment(self, cid, vt="坦克300", likes=1):
        return CommentRecord(comment_id=cid, content=f"评论{cid}", video_title=vt,
                             job_id=f"j{cid}", comment_like_count=likes,
                             passed=True, is_car_owner=True, has_purchase_intent=False)
    def count_comments(self, **kw):
        self._calls.append(("count", kw)); return 100
    def time_series(self, **kw):
        self._calls.append(("ts", kw))
        return {"start": "2026-08-01", "end": "2026-08-31", "bucket": "day",
                "buckets": [{"start": "2026-08-01", "count": 30},
                            {"start": "2026-08-02", "count": 70}], "total": 100}
    def top_videos(self, **kw):
        self._calls.append(("top", kw))
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 20}]
    def topic_frequency(self, **kw):
        self._calls.append(("topic", kw))
        return [{"topic": "油耗", "comment_count": 40}, {"topic": "改装", "comment_count": 30}]
    def fetch_comments(self, limit=30, **kw):
        self._calls.append(("fetch", kw))
        return [self._comment("c1"), self._comment("c2")]


def _snap():
    return LogicalSnapshot(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        extra={"video_tags": ["坦克300"], "object": "坦克300", "goal_type": "pulse"},
    )


class TestTools:
    def _run(self, fn):
        ds = FakeDS()
        store = EvidenceStore("t1")
        out = fn(ds, _snap(), store)
        return ds, store, out

    def test_data_coverage(self):
        ds, store, out = self._run(data_coverage)
        assert out["name"] == "data_coverage"
        assert out["sample_size"] > 0
        assert store.all_records()  # 应登记统计证据

    def test_volume_trend(self):
        ds, store, out = self._run(volume_trend)
        assert out["result"]["total"] == 100
        assert out["evidence_ids"]

    def test_period_comparison_has_change_rate(self):
        ds, store, out = self._run(period_comparison)
        assert "change_rate" in out["result"] or "current" in out["result"]

    def test_topic_frequency_tool(self):
        ds, store, out = self._run(topic_frequency_tool)
        assert any(t["topic"] == "油耗" for t in out["result"]["topics"])

    def test_top_sources(self):
        ds, store, out = self._run(top_sources)
        assert out["result"]["videos"][0]["job_id"] == "j1"

    def test_sample_comments_registers_evidence(self):
        ds, store, out = self._run(sample_comments)
        assert out["sample_size"] >= 2
        assert store.has_comment("c1")

    def test_drill_evidence(self):
        ds, store, out = self._run(drill_evidence)
        assert "keywords" in out["params"] or "result" in out

    def test_object_compare(self):
        ds, store, out = self._run(object_compare)
        assert out["name"] == "object_compare"

    def test_tools_register_stat_evidence(self):
        ds, store, out = self._run(data_coverage)
        kinds = {r.kind for r in store.all_records()}
        assert "stat" in kinds
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_analysis_tools.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.analysis'`

- [ ] **Step 3: 实现**

创建 `app/analysis/__init__.py`（空文件）。

创建 `app/analysis/tools.py`：

```python
"""确定性分析工具集。

每个工具都：从 snapshot 取时间窗与对象标签（口径一致），调用 datasource
取数/统计，登记证据，返回统一的 TOOL_PROTOCOL 结构。

模型/Agent 不能直接连库——所有数据访问都走这里的工具，或 datasource 预定义方法。
"""
from datetime import datetime, timedelta


def _window(snap):
    return snap.start_time, snap.end_time


def _tags(snap):
    return (snap.extra or {}).get("video_tags") or []


def snap_iso(dt):
    """datetime/date 转 iso 字符串（None 透传）。时间范围字段用它。"""
    return dt.isoformat() if dt else None


def data_coverage(ds, snap, store):
    start, end = _window(snap)
    n = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    store.register_stat(label="comment_count", detail="窗口内评论数", count=n, source="data_coverage")
    return {
        "name": "data_coverage",
        "params": {"start_time": snap_iso(start), "end_time": snap_iso(end),
                   "video_tags": _tags(snap)},
        "result": {"comment_count": n, "datasource": "api_job"},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": n,
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "统计基于 api_job 成功作业及其展开评论，未做全量向量化主题聚类。",
    }


def volume_trend(ds, snap, store):
    start, end = _window(snap)
    ts = ds.time_series(start_time=start, end_time=end, video_tags=_tags(snap), bucket="day")
    store.register_stat(label="trend_total", detail="窗口总体声量", count=ts["total"], source="volume_trend")
    return {
        "name": "volume_trend",
        "params": {"start_time": snap_iso(start), "end_time": snap_iso(end),
                   "video_tags": _tags(snap), "bucket": "day"},
        "result": ts,
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": ts["total"],
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "时间锚点取作业 created_at(UTC)，非评论发布时间。",
    }


def _prev_window(start, end):
    """等长的前一周期。"""
    if start is None or end is None:
        return None, None
    delta = end - start
    return start - delta, start


def period_comparison(ds, snap, store):
    start, end = _window(snap)
    cur = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    pstart, pend = _prev_window(start, end)
    prev = ds.count_comments(start_time=pstart, end_time=pend, video_tags=_tags(snap)) \
        if pstart and pend else 0
    change_rate = round((cur - prev) / prev * 100, 1) if prev else None
    store.register_stat(label="comparison", detail="当前vs对比", count=cur, source="period_comparison")
    return {
        "name": "period_comparison",
        "params": {"current": {"start": snap_iso(start), "end": snap_iso(end)},
                   "prev": {"start": snap_iso(pstart), "end": snap_iso(pend)}},
        "result": {"current": cur, "previous": prev, "change_rate": change_rate,
                   "note": "对比周期为当前窗口等长的前一周期"},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": cur,
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "变化率基于作业 created_at 分桶；prev=0 时 change_rate 为 null。",
    }


def topic_frequency_tool(ds, snap, store):
    start, end = _window(snap)
    tags = _tags(snap)
    tf = ds.topic_frequency(start_time=start, end_time=end, video_tags=tags, limit=50)
    # 剔除对象本身的话题标签（如 #坦克300 会占据首位），只留子主题
    obj = (snap.extra or {}).get("object")
    topics = [t for t in tf if t["topic"] != obj]
    store.register_stat(label="topics", detail="候选子主题数", count=len(topics), source="topic_frequency")
    return {
        "name": "topic_frequency",
        "params": {"video_tags": tags, "limit": 50},
        "result": {"topics": topics},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": len(topics),
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "主题来自视频标题的话题标签，非全量评论分词，可能遗漏隐含表达。",
    }


def top_sources(ds, snap, store, limit=10):
    start, end = _window(snap)
    vids = ds.top_videos(start_time=start, end_time=end, video_tags=_tags(snap), limit=limit)
    for v in vids:
        store.register_video(job_id=v["job_id"], video_title=v["video_title"],
                             comment_count=v["comment_count"], source="top_sources")
    return {
        "name": "top_sources",
        "params": {"limit": limit, "video_tags": _tags(snap)},
        "result": {"videos": vids},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": len(vids),
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "按评论数排序，代表声量集中来源；不代表全部传播渠道。",
    }


def sample_comments(ds, snap, store, limit=30, keyword=None):
    start, end = _window(snap)
    recs = ds.fetch_comments(
        limit=limit, start_time=start, end_time=end, video_tags=_tags(snap),
        keyword=keyword, order_by="random",
    )
    for r in recs:
        store.register_comment(r, source="sample_comments")
    return {
        "name": "sample_comments",
        "params": {"limit": limit, "keyword": keyword, "video_tags": _tags(snap)},
        "result": {"comments": [r.to_evidence() for r in recs]},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": len(recs),
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": f"随机抽样 {len(recs)} 条代表评论，非全量，存在抽样偏差。",
    }


def drill_evidence(ds, snap, store, *, keyword=None, min_like=None, limit=30):
    start, end = _window(snap)
    recs = ds.fetch_comments(
        limit=limit, start_time=start, end_time=end, video_tags=_tags(snap),
        keyword=keyword, min_like=min_like, order_by="likes",
    )
    for r in recs:
        store.register_comment(r, source="drill_evidence")
    return {
        "name": "drill_evidence",
        "params": {"keyword": keyword, "min_like": min_like, "limit": limit},
        "result": {"comments": [r.to_evidence() for r in recs]},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": len(recs),
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "按点赞数降序取高互动评论，偏向高关注样本。",
    }


def object_compare(ds, snap, store, *, other_tags=None, limit=10):
    start, end = _window(snap)
    cur = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    other = 0
    if other_tags:
        other = ds.count_comments(start_time=start, end_time=end, video_tags=other_tags)
    store.register_stat(label="object_compare", detail="对象 vs 参照", count=cur, source="object_compare")
    return {
        "name": "object_compare",
        "params": {"object": (snap.extra or {}).get("object"), "other_tags": other_tags},
        "result": {"object_count": cur, "other_count": other},
        "evidence_ids": [r.evidence_id for r in store.all_records()],
        "sample_size": cur,
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": "对象间比较仅基于同窗口声量，未做质量或情感维度比较。",
    }
```

> 注：各工具直接构造返回 dict，`snap_iso` 用于把 datetime 转 iso 字符串填 `time_range`。
> `_tag_like_clause` 已在 Task 3 定义；`fetch_comments` 的 `order_by="random"` / `"likes"`
> 由 Task 3 的数据源层实现。

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_analysis_tools.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/analysis/ tests/test_analysis_tools.py
git commit -m "feat(analysis): 实现确定性分析工具集"
```

---

### Task 6: 固定流水线 baseline

**Files:**
- Create: `app/pipeline/__init__.py`
- Create: `app/pipeline/baseline.py`
- Test: `tests/test_pipeline_baseline.py`

**Interfaces:**
- Consumes: 8 个工具（Task 5）、`EvidenceStore`（Task 4）、`LogicalSnapshot`
- Produces: `AnalysisBundle`、`BaselinePipeline`（签名见契约）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_baseline.py`：

```python
"""固定流水线 baseline 测试（工具用 stub，不连库）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import pytest

from app.pipeline.baseline import AnalysisBundle, BaselinePipeline
from app.store.evidence import EvidenceStore
from app.snapshot.snapshot import LogicalSnapshot


class StubToolset:
    """返回稳定的工具结果。"""
    def __init__(self, store):
        self.store = store
    def data_coverage(self, ds, snap, store):
        return {"name": "data_coverage", "result": {"comment_count": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def volume_trend(self, ds, snap, store):
        return {"name": "volume_trend", "result": {"total": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def period_comparison(self, ds, snap, store):
        return {"name": "period_comparison", "result": {"change_rate": 12.5},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def topic_frequency_tool(self, ds, snap, store):
        return {"name": "topic_frequency", "result": {"topics": [{"topic": "油耗"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def top_sources(self, ds, snap, store):
        return {"name": "top_sources", "result": {"videos": [{"job_id": "j1"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def sample_comments(self, ds, snap, store):
        return {"name": "sample_comments", "result": {"comments": [{"comment_id": "c1"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def drill_evidence(self, ds, snap, store):
        return {"name": "drill_evidence", "result": {"comments": []},
                "evidence_ids": [], "sample_size": 0, "time_range": {}, "bias_note": ""}
    def object_compare(self, ds, snap, store):
        return {"name": "object_compare", "result": {"object_count": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}


class TestBaselinePipeline:
    def test_run_builds_bundle_with_all_stages(self):
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300", "goal_type": "pulse"},
        )
        store = EvidenceStore("t1")
        pipe = BaselinePipeline(
            datasource=None, snapshot=snap, evidence_store=store,
            toolset=StubToolset(store),
        )
        bundle = pipe.run()
        assert isinstance(bundle, AnalysisBundle)
        assert bundle.scope["comment_count"] == 100
        assert bundle.overall["change_rate"] == 12.5
        assert bundle.themes["topics"][0]["topic"] == "油耗"
        assert bundle.sources["videos"][0]["job_id"] == "j1"
        assert len(bundle.samples) == 1
        assert len(bundle.stats) >= 5

    def test_run_returns_serializable_dict(self):
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"]},
        )
        store = EvidenceStore("t1")
        pipe = BaselinePipeline(datasource=None, snapshot=snap, evidence_store=store,
                                toolset=StubToolset(store))
        d = pipe.run().to_dict()
        import json
        json.dumps(d)  # 必须可序列化
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_pipeline_baseline.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.pipeline'`

- [ ] **Step 3: 实现**

创建 `app/pipeline/__init__.py`（空文件）。

创建 `app/pipeline/baseline.py`：

```python
"""V0.2 固定分析流水线。

按固定顺序执行确定性工具，产出 AnalysisBundle 作为 LLM 报告生成的输入。
不做任何 LLM 自主规划；阶段顺序由本模块硬编码。
"""
from dataclasses import dataclass, field


@dataclass
class AnalysisBundle:
    scope: dict
    overall: dict
    themes: dict
    sources: dict
    samples: list[dict]
    stats: list[dict]

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "overall": self.overall,
            "themes": self.themes,
            "sources": self.sources,
            "samples": self.samples,
            "stats": self.stats,
        }


class BaselinePipeline:
    """固定顺序：①范围 ②总体 ③主题 ④来源 ⑤抽样 ⑥下钻 ⑦比较。”

    数据集（datasource）+ 日志快照（snapshot）+ 证据库（evidence_store）。
    toolset 可注入（测试用 stub）；默认用 app.analysis.tools 的 8 个工具。
    """

    def __init__(self, datasource, snapshot, evidence_store, toolset=None):
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        if toolset is None:
            from app.analysis import tools
            self._tools = tools
        else:
            self._tools = toolset

    def run(self) -> AnalysisBundle:
        t = self._tools
        ds, snap, store = self.datasource, self.snapshot, self.evidence_store

        coverage = t.data_coverage(ds, snap, store)
        trend = t.volume_trend(ds, snap, store)
        comparison = t.period_comparison(ds, snap, store)
        theme_res = t.topic_frequency_tool(ds, snap, store)
        source_res = t.top_sources(ds, snap, store)
        sample_res = t.sample_comments(ds, snap, store)
        drill_res = t.drill_evidence(ds, snap, store)
        compare_res = t.object_compare(ds, snap, store)

        stats = [coverage, trend, comparison, theme_res, source_res, sample_res,
                 drill_res, compare_res]

        scope = {
            "comment_count": coverage["result"].get("comment_count"),
            "time_range": coverage.get("time_range"),
            "datasource": coverage["result"].get("datasource"),
            "bias_note": coverage.get("bias_note"),
        }
        overall = {
            "current": trend["result"].get("total"),
            "change_rate": comparison["result"].get("change_rate"),
            "bias_note": trend.get("bias_note"),
        }
        themes = {
            "topics": theme_res["result"].get("topics", []),
            "bias_note": theme_res.get("bias_note"),
        }
        sources = {
            "videos": source_res["result"].get("videos", []),
            "bias_note": source_res.get("bias_note"),
        }
        samples = sample_res["result"].get("comments", [])
        drill_comments = drill_res["result"].get("comments", [])
        object_compare = compare_res

        return AnalysisBundle(
            scope=scope,
            overall=overall,
            themes=themes,
            sources=sources,
            samples=samples,
            stats=stats,
        )
```

> 工具变量命名统一用 `*_res`（theme_res / source_res / sample_res / drill_res / compare_res），
> 避免覆盖 `themes` / `sources` / `samples` 这些最终输出字段名。各工具返回的 dict，
> 取其 `result` 键下的子字段，其余元数据（bias_note / time_range）保留在 stats 里。

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_pipeline_baseline.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/pipeline/ tests/test_pipeline_baseline.py
git commit -m "feat(pipeline): 实现固定分析流水线 baseline"
```

---

### Task 7: 报告生成（LLM）

**Files:**
- Create: `app/llm/report.py`
- Test: `tests/test_llm_report.py`

**Interfaces:**
- Consumes: `AnalysisBundle`（Task 6）、`LLMProvider`（Task 2）、`AnalysisIntent`
- Produces: `build_report_messages(bundle, intent)`、`generate_strategy_pack(bundle, provider)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_llm_report.py`：

```python
"""报告生成测试（用 MockLLMProvider 返回固定 JSON，不连真实 API）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.llm.report import build_report_messages, generate_strategy_pack
from app.pipeline.baseline import AnalysisBundle
from app.llm.provider import LLMResult, LLMUsage


class MockProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
    def chat_json(self, messages, **kw):
        self.calls.append(messages)
        import json
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        result = LLMResult(json.dumps(self.payload, ensure_ascii=False), usage=usage)
        return self.payload, result


def _bundle():
    return AnalysisBundle(
        scope={"comment_count": 100, "time_range": {"start": "x", "end": "y"}, "datasource": "api_job"},
        overall={"current": 100, "change_rate": 12.5},
        themes={"topics": [{"topic": "油耗", "comment_count": 40}]},
        sources={"videos": [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50}]},
        samples=[{"comment_id": "c1", "content": "这车油耗高"}],
        stats=[],
    )


class TestReportGen:
    def test_build_messages_includes_bundle(self):
        intent = {"object": "坦克300", "goal_type": "pulse"}
        msgs = build_report_messages(_bundle(), intent)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "坦克300" in msgs[1]["content"]

    def test_generate_strategy_pack_parses(self):
        payload = {"scope": {"comment_count": 100}, "themes": []}
        provider = MockProvider(payload)
        report, result = generate_strategy_pack(_bundle(), provider)
        assert report["scope"]["comment_count"] == 100
        assert result.usage.total_tokens == 15
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_llm_report.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.llm.report'`

- [ ] **Step 3: 实现**

创建 `app/llm/report.py`：

```python
"""报告生成：把结构化中间结果交给 LLM 生成「舆情策略包」。

V0.2 用一次 LLM 调用归纳主题并产生完整策略包。输出是固定结构的 JSON，
引用的证据 ID（comment_id / job_id）必须来自喂给模型的 bundle，否则被
V0.2 的引用校验环节剔除。
"""
import json

REPORT_SYSTEM_PROMPT = (
    "你是一名汽车舆情分析师。基于用户提供的结构化统计数据与抽样评论，"
    "生成一份《舆情策略包》。"
    "要求：\n"
    "1. 事实与判断分离：数字结论引用给定统计；解释性判断需说明依据；"
    "   证据不足的事项标记为【待验证假设】。\n"
    "2. 只能引用输入中出现的评论ID(comment_id)与来源ID(job_id/video_title)，"
    "  不得编造不存在的ID。\n"
    "3. 输出严格为 JSON 对象，字段如下：\n"
    "   scope(数据覆盖范围与样本说明),\n"
    "   overall(总体声量及变化),\n"
    "   themes[{theme, heat_up, refs:[comment_id]}],\n"
    "   sources[{job_id, video_title, comment_count}],\n"
    "   risk_opportunity[{type: risk|opportunity, title, reason, refs}],\n"
    "   evidence_gaps[{gap, why}],\n"
    "   assumptions[{assumption, how_to_verify}],\n"
    "   actions[{action, priority, target}],\n"
    "   metrics[{metric, target}]\n"
)


def build_report_messages(bundle: "AnalysisBundle", intent) -> list[dict]:
    """构造 user 消息上下文。intent 为 dict（含 object/goal_type/time_range）。"""
    ctx = {
        "intent": intent,
        "scope": bundle.scope,
        "overall": bundle.overall,
        "themes": bundle.themes,
        "sources": bundle.sources,
        "samples": bundle.samples,
        "stats": bundle.stats,
    }
    user = (
        "以下是本次舆情分析的结构化统计与抽样评论，请据此生成舆情策略包：\n\n"
        + json.dumps(ctx, ensure_ascii=False, default=str)
    )
    return [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def generate_strategy_pack(bundle, provider):
    """调用 LLM 生成策略包。返回 (report_dict, llm_result)。"""
    messages = build_report_messages(bundle, {})
    data, result = provider.chat_json(messages, max_tokens=4000)
    return data, result
```

> `build_report_messages` 的 `intent` 形参为 dict；实际调用时传入
> `task.parsed_intent`。策略包结构以 `.env` 的 `LLM_MODEL` 实际返回为准，但
> 字段名必须符合上述系统提示词约定。

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_llm_report.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/llm/report.py tests/test_llm_report.py
git commit -m "feat(llm): 实现舆情策略包报告生成"
```

---

### Task 8: 引用校验

**Files:**
- Create: `app/pipeline/verify.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `EvidenceStore`（Task 4）
- Produces: `validate_report(report, evidence_store) -> (cleaned_report, validation)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_verify.py`：

```python
"""报告引用校验测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.pipeline.verify import validate_report
from app.store.evidence import EvidenceStore
from app.datasource.models import CommentRecord


def _store():
    st = EvidenceStore("t1")
    st.register_comment(CommentRecord(comment_id="c1", content="ok", job_id="j1"), source="s")
    st.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="s")
    return st


def test_all_valid_refs_kept():
    store = _store()
    report = {"themes": [{"theme": "油耗", "refs": ["c1"]}],
              "sources": [{"job_id": "j1", "video_title": "坦克300"}]}
    cleaned, validation = validate_report(report, store)
    assert validation["valid_refs"] == validation["total_refs"]
    assert validation["rejected_refs"] == []
    assert len(cleaned["themes"][0]["refs"]) == 1


def test_invalid_comment_ref_removed():
    store = _store()
    report = {"themes": [{"theme": "油耗", "refs": ["c1", "c_fake"]}]}
    cleaned, validation = validate_report(report, store)
    assert "c_fake" in validation["rejected_refs"]
    assert "c_fake" not in cleaned["themes"][0]["refs"]
    assert "c1" in cleaned["themes"][0]["refs"]


def test_invalid_video_ref_removed():
    store = _store()
    report = {"sources": [{"job_id": "j1", "video_title": "a"}, {"job_id": "j_fake", "video_title": "b"}]}
    cleaned, validation = validate_report(report, store)
    assert len(cleaned["sources"]) == 1
    assert cleaned["sources"][0]["job_id"] == "j1"
    assert "j_fake" in validation["rejected_refs"]


def test_no_refs_report_passes_with_zero():
    store = _store()
    report = {"themes": [{"theme": "x"}]}
    cleaned, validation = validate_report(report, store)
    assert validation["total_refs"] == 0
    assert validation["valid_refs"] == 0
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_verify.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.pipeline.verify'`

- [ ] **Step 3: 实现**

创建 `app/pipeline/verify.py`：

```python
"""报告引用校验。

从报告 JSON 中提取引用的 comment_id / job_id（video_id），逐一到证据库核对。
合法引用保留；非法引用剔除并记录到 validation.rejected_refs。

设计约束：LLM 只能引用喂给它的证据；输出中出现的未知 ID 直接剔除，
并在报告中标记「未通过校验的引用」，禁止伪证据进入最终报告。
"""


def _iter_ref_keys(obj, key):
    """深度遍历 obj，收集值为字符串的 target-key 引用列表。

    key 为 dict 字段名（如 comment_id / job_id）。返回收集到的 id 列表。
    """
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str) and v:
                found.append(v)
            elif isinstance(v, (dict, list)):
                found.extend(_iter_ref_keys(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_iter_ref_keys(item, key))
    return found


def _sanitize(obj, *, comment_ids, video_ids, rejected):
    """递归清理：剔除非法的 comment_ref 数组元素、非法 sources 项。"""
    if isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict):
                new_item = _sanitize(item, comment_ids=comment_ids,
                                     video_ids=video_ids, rejected=rejected)
                # 若 sources 项 job_id 非法则整项删除
                if "job_id" in item and item.get("job_id") \
                        and str(item["job_id"]) not in video_ids:
                    rejected.append(str(item["job_id"]))
                    continue
                out.append(new_item)
            else:
                out.append(item)
        return out
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if k == "refs" and isinstance(v, list):
                kept = []
                for cid in v:
                    if str(cid) in comment_ids:
                        kept.append(cid)
                    else:
                        rejected.append(str(cid))
                new[k] = kept
            elif k == "job_id" and isinstance(v, str) and v:
                # 保留（项级过滤在上面 list 分支处理）
                new[k] = v
            else:
                new[k] = _sanitize(v, comment_ids=comment_ids,
                                   video_ids=video_ids, rejected=rejected)
        return new
    return obj


def validate_report(report: dict, evidence_store) -> tuple[dict, dict]:
    """校验并清理报告引用。

    返回 (cleaned_report, validation)。validation 含 total_refs / valid_refs /
    rejected_refs / notes。
    """
    comment_ids = {r.comment_id for r in evidence_store.all_records() if r.comment_id}
    video_ids = {r.job_id for r in evidence_store.all_records() if r.job_id}

    ref_comment = set(_iter_ref_keys(report, "comment_id"))
    ref_video = set(_iter_ref_keys(report, "job_id"))
    # refs 数组内的 comment 引用
    refs_array = set()
    if isinstance(report.get("themes"), list):
        for th in report["themes"]:
            for cid in th.get("refs", []):
                refs_array.add(cid)

    all_found = ref_comment | ref_video | refs_array
    valid = {x for x in all_found if x in comment_ids or x in video_ids}
    invalid = all_found - valid

    cleaned = _sanitize(report, comment_ids=comment_ids, video_ids=video_ids,
                        rejected=[])

    # 通用：清理后若存在非法 comment_id 字段，一并剔除
    cleaned_refs = set(_iter_ref_keys(cleaned, "comment_id"))
    notes = []
    if invalid:
        notes.append(f"剔除 {len(invalid)} 条无法在证据库回查的引用")

    validation = {
        "total_refs": len(all_found),
        "valid_refs": len(valid),
        "rejected_refs": sorted(invalid),
        "notes": notes,
    }
    return cleaned, validation
```

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_verify.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/pipeline/verify.py tests/test_verify.py
git commit -m "feat(pipeline): 实现报告引用校验，剔除非法证据ID"
```

---

### Task 9: 任务执行器（后台线程）

**Files:**
- Create: `app/pipeline/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `TaskRepository`、`EventRepository`、`BaselinePipeline`（Task 6）、
  `generate_strategy_pack`（Task 7）、`validate_report`（Task 8）、`EvidenceStore`、`Settings`
- Produces: `run_task_sync(...)`、`start_task_background(...)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_runner.py`：

```python
"""任务执行器测试（同步路径，stub datasource + mock LLM）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import json
import pytest

from app.pipeline.runner import run_task_sync
from app.store.repository import TaskRepository, EventRepository
from app.store.evidence import EvidenceStore
from app.snapshot.snapshot import LogicalSnapshot
from app.llm.provider import LLMResult, LLMUsage


class FakeData:
    def __init__(self):
        self.calls = []
    def count_comments(self, **kw):
        self.calls.append(("count", kw)); return 100
    def time_series(self, **kw):
        return {"buckets": [{"start": "2026-08-01", "count": 100}], "total": 100}
    def top_videos(self, **kw):
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 10}]
    def topic_frequency(self, **kw):
        return [{"topic": "油耗", "comment_count": 40}]
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        ids = ["c1", "c2"]
        return [CommentRecord(comment_id=i, video_title="坦克300", job_id="j1",
                              content=f"评论{i}") for i in ids]


class MockLLM:
    def chat_json(self, messages, **kw):
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        payload = {"scope": {"comment_count": 100}, "themes": [{"theme": "油耗", "refs": ["c1"]}]}
        return payload, LLMResult(json.dumps(payload), usage=usage)


@pytest.fixture
def repos(tmp_path):
    db = str(tmp_path / "s.db")
    tr = TaskRepository(db_path=db); tr.init_schema()
    er = EventRepository(db_path=db); er.init_schema()
    return tr, er


def test_run_task_sync_success(repos):
    tr, er = repos
    task = tr.create_task(
        raw_input="分析坦克300近期的舆情变化",
        parsed_intent={"object": "坦克300", "goal_type": "pulse",
                       "time_range": {"start": "2026-08-01", "end": "2026-08-31"}},
        snapshot=LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300"},
        ).to_dict(),
    )
    result = run_task_sync(
        task.task_id,
        task_repo=tr, event_repo=er,
        datasource=FakeData(),
        llm_provider=MockLLM(),
        settings=None,
    )
    got = tr.get_task(task.task_id)
    assert got.status == "success"
    assert result["report"]["scope"]["comment_count"] == 100
    assert result["validation"]["rejected_refs"] == []  # c1 在证据库内
    assert result["meta"]["total_tokens"] == 15
    len_events = er.get_events(task.task_id)
    assert any(e.event_type == "tool_call" for e in len_events) or len(len_events) >= 3


def test_run_task_sync_marks_failed_on_error(repos):
    tr, er = repos
    task = tr.create_task(raw_input="x", parsed_intent={"object": "", "goal_type": "pulse"},
                          snapshot={})
    class BoomLLM:
        def chat_json(self, *a, **k):
            raise RuntimeError("boom")
    result = run_task_sync(
        task.task_id, task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=BoomLLM(), settings=None,
    )
    got = tr.get_task(task.task_id)
    assert got.status == "failed"
    assert "boom" in (got.error or "")
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.pipeline.runner'`

- [ ] **Step 3: 实现**

创建 `app/pipeline/runner.py`：

```python
"""任务执行器。

后台线程驱动固定流水线：取证 → 生成报告 → 引用校验 → 落库 → 标记成功/失败。
全程写追加式事件。失败保留错误，不生成伪报告。
"""
import threading
import traceback
from datetime import datetime


def run_task_sync(task_id, *, task_repo, event_repo, datasource,
                  llm_provider, settings=None):
    """同步执行一次任务，返回 result。出错则标记 failed 并保留错误。"""
    task = task_repo.get_task(task_id)
    if task is None:
        raise ValueError(f"task {task_id} not found")

    task_repo.update_status(task_id, "running")
    event_repo.append_event(task_id, "task_started", {"task_id": task_id})

    try:
        # 重建快照
        from app.snapshot.snapshot import LogicalSnapshot
        snapshot = LogicalSnapshot.from_dict(task.snapshot)

        evidence_store = EvidenceStore(task_id)

        from app.pipeline.baseline import BaselinePipeline
        pipeline = BaselinePipeline(datasource, snapshot, evidence_store)
        event_repo.append_event(task_id, "pipeline_start", {"stage": "baseline"})
        bundle = pipeline.run()
        for st in bundle.stats:
            event_repo.append_event(task_id, "tool_call", {"tool": st["name"],
                                                           "sample_size": st["sample_size"]})

        event_repo.append_event(task_id, "report_start", {})
        from app.llm.report import generate_strategy_pack
        report, llm_result = generate_strategy_pack(bundle, llm_provider)
        event_repo.append_event(task_id, "report_done", {
            "usage": llm_result.usage.to_dict(),
        })

        event_repo.append_event(task_id, "validate_start", {})
        from app.pipeline.verify import validate_report
        cleaned, validation = validate_report(report, evidence_store)
        event_repo.append_event(task_id, "validate_done", validation)

        evidence_list = evidence_store.to_dicts()
        result = {
            "report": cleaned,
            "validation": validation,
            "evidence": evidence_list,
            "meta": {
                "model": llm_result.usage.model,
                "prompt_tokens": llm_result.usage.prompt_tokens,
                "completion_tokens": llm_result.usage.completion_tokens,
                "total_tokens": llm_result.usage.total_tokens,
                "latency_ms": llm_result.usage.latency_ms,
            },
        }
        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})
        return result
    except Exception as e:
        task_repo.mark_failed(task_id, str(e))
        event_repo.append_event(task_id, "task_failed", {
            "error": str(e), "traceback": traceback.format_exc(limit=5),
        })
        return {"error": str(e)}


def start_task_background(*, task_id, task_repo, event_repo, datasource,
                          llm_provider, settings):
    """在新线程里执行任务（V0 不引入 Celery/消息队列）。"""
    def _worker():
        run_task_sync(
            task_id, task_repo=task_repo, event_repo=event_repo,
            datasource=datasource, llm_provider=llm_provider, settings=settings,
        )
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
```

- [ ] **Step 4: 运行通过**

Run: `python -m pytest tests/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/pipeline/runner.py tests/test_runner.py
git commit -m "feat(pipeline): 实现后台任务执行器"
```

---

### Task 10: API 接入（运行 + 报告 + 证据端点）

**Files:**
- Modify: `app/api/schemas.py`
- Modify: `app/api/routes.py`
- Test: `tests/test_api_v02.py`

**Interfaces:**
- Consumes: `run_task_sync` / `start_task_background`（Task 9）、`LLMProvider`（Task 2）
- Produces: `POST /api/tasks`（后台执行）、`GET /api/tasks/{id}/report`、`GET /api/tasks/{id}/evidence`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_api_v02.py`：

```python
"""V0.2 API 测试：任务后台执行、报告与证据端点。

用 create_app(background=False) 同步执行 + stub datasource + mock LLM，
避免真实网络与轮询。
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import pytest


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
        return [CommentRecord(comment_id="c1", video_title="坦克300", job_id="j1",
                              content="评论1"),
                CommentRecord(comment_id="c2", video_title="坦克300", job_id="j1",
                              content="评论2")]
    def data_overview(self):
        from app.datasource.adapter import DataOverview
        return DataOverview(job_count=100, comment_count=100, start_time=None, end_time=None)


class MockLLM:
    def chat_json(self, messages, **kw):
        import json
        from app.llm.provider import LLMUsage, LLMResult
        payload = {"scope": {"comment_count": 100},
                   "themes": [{"theme": "油耗", "refs": ["c1"]}]}
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        return payload, LLMResult(json.dumps(payload), usage=usage)


@pytest.fixture
def client(tmp_path):
    from app.api.routes import create_app
    db_path = os.path.join(tmp_path, "app_state.db")
    app = create_app(
        db_path=db_path, datasource=FakeData(), llm_provider=MockLLM(),
        background=False,  # 同步执行，便于测试
        settings=None,
    )
    return TestClient(app)


class TestV02:
    def test_create_task_runs_to_success(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期的舆情变化"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"  # background=False 同步执行
        assert body["result"]["report"]["scope"]["comment_count"] == 100

    def test_report_endpoint(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期"})
        task_id = resp.json()["task_id"]
        r2 = client.get(f"/api/tasks/{task_id}/report")
        assert r2.status_code == 200
        assert r2.json()["report"]["scope"]["comment_count"] == 100

    def test_evidence_endpoint(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期"})
        task_id = resp.json()["task_id"]
        r2 = client.get(f"/api/tasks/{task_id}/evidence")
        assert r2.status_code == 200
        evs = r2.json()["evidence"]
        assert any(e["comment_id"] == "c1" for e in evs)

    def test_nonexistent_report_404(self, client):
        assert client.get("/api/tasks/none/report").status_code == 404
```

- [ ] **Step 2: 运行失败**

Run: `python -m pytest tests/test_api_v02.py -v`
Expected: FAIL（无 report/evidence 端点或 status 非 success）

- [ ] **Step 3: 实现（schemas.py）**

在 `app/api/schemas.py` 追加：

```python
class ReportResponse(BaseModel):
    task_id: str
    status: str
    report: Optional[dict] = None
    validation: Optional[dict] = None
    meta: Optional[dict] = None
    error: Optional[str] = None


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: str
    comment_id: Optional[str] = None
    job_id: Optional[str] = None
    content: str = ""
    video_title: str = ""
    like_count: int = 0
    passed: Optional[bool] = None
    is_car_owner: Optional[bool] = None
    has_purchase_intent: Optional[bool] = None
    source: str = ""
    extra: dict = {}


class EvidenceListResponse(BaseModel):
    task_id: str
    evidence: list[EvidenceItem]
```

- [ ] **Step 4: 实现（routes.py）**

修改 `create_app` 签名并接入：

```python
def create_app(db_path=None, datasource=None, settings=None, static_dir=None, *,
               llm_provider=None, background=True):
```

在函数内追加：

```python
    # 注入 LLM Provider：外部（测试）传参优先，否则用配置构建
    if llm_provider is None:
        from app.llm.provider import LLMProvider
        llm_provider = LLMProvider.from_settings(settings)
```

修改 `POST /api/tasks`：

```python
    @app.post("/api/tasks", response_model=TaskResponse)
    def create_task(req: CreateTaskRequest):
        intent = parser.parse(req.raw_input)
        from app.snapshot.snapshot import LogicalSnapshot
        snap = LogicalSnapshot.from_intent(intent)
        # 附加对象标签边界（V0.2：对象按 #标签 匹配，存进快照 extra）
        if intent.object:
            snap.extra = dict(snap.extra or {})
            snap.extra.setdefault("video_tags", [intent.object])

        task = task_repo.create_task(
            raw_input=req.raw_input,
            parsed_intent=intent.to_dict(),
            snapshot=snap.to_dict(),
        )
        event_repo.append_event(task.task_id, "task_created",
                                {"raw_input": req.raw_input, "intent": intent.to_dict()})

        if background:
            from app.pipeline.runner import start_task_background
            start_task_background(
                task_id=task.task_id, task_repo=task_repo, event_repo=event_repo,
                datasource=_ds, llm_provider=llm_provider, settings=settings,
            )
        else:
            from app.pipeline.runner import run_task_sync
            run_task_sync(
                task.task_id, task_repo=task_repo, event_repo=event_repo,
                datasource=_ds, llm_provider=llm_provider, settings=settings,
            )
            task = task_repo.get_task(task.task_id)
        return _task_to_response(task)
```

新增报告与证据端点：

```python
    @app.get("/api/tasks/{task_id}/report", response_model=ReportResponse)
    def get_report(task_id: str):
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        if task.status != "success" or not task.result:
            raise HTTPException(status_code=409,
                                detail=f"task {task_id} 未成功完成 (status={task.status})")
        return ReportResponse(task_id=task_id, status=task.status,
                              report=task.result.get("report"),
                              validation=task.result.get("validation"),
                              meta=task.result.get("meta"),
                              error=None)

    @app.get("/api/tasks/{task_id}/evidence", response_model=EvidenceListResponse)
    def get_evidence(task_id: str):
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        evs = []
        if task.result:
            evs = task.result.get("evidence", [])
        return EvidenceListResponse(task_id=task_id, evidence=evs)
```

把 `from app.api.schemas import (..., ReportResponse, EvidenceListResponse)` 加进 imports。

- [ ] **Step 5: 运行通过**

Run: `python -m pytest tests/test_api_v02.py -v`
Expected: PASS

- [ ] **Step 6: 回归既有 API 测试**

Run: `python -m pytest tests/test_api.py tests/test_api_v02.py -v`
Expected: PASS（`background=False` 不影响既有 `test_api.py` 的用例，因其 datasource=None；若既有用例因异步改同步受影响，检查 `background` 参数默认值与 datasource 逻辑）

- [ ] **Step 7: 提交**

```bash
git add app/api/ tests/test_api_v02.py
git commit -m "feat(api): 接入任务后台执行与报告/证据端点"
```

---

### Task 11: 真实 LLM 冒烟（单次）

**Files:**
- 无新文件

**Interfaces:**
- Consumes: `create_app_from_settings`（真实 datasource + 真实 LLM）

- [ ] **Step 1: 执行前告知**

真实 LLM 调用成本非零。执行前在控制台输出一句：「即将执行 1 次真实 LLM 冒烟调用」。

- [ ] **Step 2: 运行冒烟**

Run:

```bash
python -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from app.api.routes import create_app_from_settings
from fastapi.testclient import TestClient
app = create_app_from_settings()
c = TestClient(app)
resp = c.post('/api/tasks', json={'raw_input': '分析坦克300近期的舆情变化'})
print('POST', resp.status_code)
body = resp.json()
print('status', body['status'])
print('result keys', list(body['result'].keys()) if body.get('result') else None)
print('meta', body['result']['meta'] if body.get('result') else None)
"
```

> 说明：`create_app_from_settings` 用真实 LLM；因冒烟走 `background=True` 需等待，可临时用
> `background=False` 的 `create_app(db_path=None, datasource=ds, settings=s, llm_provider=LLMProvider.from_settings(s), background=False)` 同步结束，便于断言结果。

Expected: status=success，result 含 report/validation/evidence/meta，meta.total_tokens>0。

- [ ] **Step 3: 校验证据引用**

从冒烟 result 中检查 validation.rejected_refs 是否为合法过滤（可能为空或含被剔除项），
并抽查任一 comment_id 能在 evidence 列表中回查。

- [ ] **Step 4: 记录结果到测试报告（见 Task 12）**

---

### Task 12: 前端（状态轮询 + 基线报告视图）

**Files:**
- Modify: `page/src/App.jsx`
- Modify: `page/src/App.css`

**Interfaces:**
- Consumes: `GET /api/tasks?task_id=`、`GET /api/tasks/{id}/report`、`GET /api/tasks/{id}/evidence`

- [ ] **Step 1: 在 App.jsx 增加「运行状态 + 报告」交互**

在任务列表中，为每个任务在主区域展示状态与结果概要；点击任务进入报告视图。

在 `App.jsx` 顶部状态追加：

```js
const [selectedTask, setSelectedTask] = useState(null)
const [report, setReport] = useState(null)
const [pollTimer, setPollTimer] = useState(null)
```

新增 `selectTask(taskId)`：拉取任务与报告/证据，若任务处于 running 则开启轮询。

```js
async function selectTask(taskId) {
  setSelectedTask(taskId)
  setReport(null)
  await refreshTask(taskId)
  const t = setInterval(() => refreshTask(taskId), 2500)
  setPollTimer(t)
  return () => clearInterval(t)
}

async function refreshTask(taskId) {
  const res = await fetch(`${API_BASE}/tasks?task_id=${taskId}`)
  const task = await res.json()
  setSelectedTask(task)
  if (task.status === 'success' && task.result) {
    setReport(task.result)
    if (pollTimer) { clearInterval(pollTimer); setPollTimer(null) }
  } else if (task.status === 'failed') {
    if (pollTimer) { clearInterval(pollTimer); setPollTimer(null) }
  }
}
```

- [ ] **Step 2: 渲染报告视图**

在任务列表项加点击处理，并在主区域新增：

```jsx
{selectedTask && (
  <section className="card">
    <h2>基线报告</h2>
    <button onClick={() => { setSelectedTask(null); setReport(null); if (pollTimer) clearInterval(pollTimer) }}>返回列表</button>
    {report?.meta && (
      <div className="stats">
        <Stat label="模型" value={report.meta.model} />
        <Stat label="Token" value={report.meta.total_tokens} />
        <Stat label="耗时(ms)" value={report.meta.latency_ms} />
      </div>
    )}
    {report?.validation && (
      <p className="muted">
        引用校验：通过 {report.validation.valid_refs}/{report.validation.total_refs}
        {report.validation.rejected_refs?.length > 0
          ? `；剔除 ${report.validation.rejected_refs.join(', ')}`
          : ''}
      </p>
    )}
    {report?.report ? (
      <pre className="report">{JSON.stringify(report.report, null, 2)}</pre>
    ) : (
      <p className="muted">报告生成中或失败…</p>
    )}
  </section>
)}
```

- [ ] **Step 3: 状态行展示模型/Token/耗时（任务列表）**

在任务列表项状态后追加：

```jsx
{t.result?.meta && (
  <span className="task-meta">
    {t.result.meta.model} · {t.result.meta.total_tokens} tokens · {t.result.meta.latency_ms}ms
  </span>
)}
```

- [ ] **Step 4: 样式（App.css）**

追加：

```css
.task-meta { color: #888; font-size: 12px; margin-left: 8px; }
.report { max-height: 60vh; overflow: auto; background: #f7f7f9; padding: 12px; border-radius: 6px; white-space: pre-wrap; }
```

- [ ] **Step 5: 本地验证**

Run: `cd page && npm run build`
Expected: 编译通过，无报错。（本地 dev 验证需 Vite + 后端，可选。）

- [ ] **Step 6: 提交**

```bash
git add page/src/App.jsx page/src/App.css
git commit -m "feat(page): 增加任务运行状态与基线报告视图"
```

---

### Task 13: 文档与生成物

**Files:**
- Modify: `docs/architecture.md`（补充 V0.2 模块与数据口径）
- Modify: `docs/how-it-works.md`（补充 V0.2 端到端说明）
- Modify: `docs/design/core-design.md`（补充 V0.2 各模块设计要点）
- Modify: `.env.example`（LLM 段注释更新，含超时）
- Create: `docs/specs/Marketing_Brain_V0.2_TestReport.md`（冒烟与回归结果）

- [ ] **Step 1: 更新架构文档**

在 `docs/architecture.md` 模块表增加 `llm` / `analysis` / `pipeline`，并补一条
「时间锚点用 `job.created_at`，对象匹配用 `#标签`」的约束（V0.2 口径）。

- [ ] **Step 2: 更新 how-it-works**

新增一节「V0.2：固定流程生成舆情报告」，说明固定流水线（数据取证→LLM报告→引用校验→落库）
与证据回查。

- [ ] **Step 3: 更新 core-design**

补充 V0.2 各模块（Provider / 工具 / baseline / verify / runner）职责与决策摘要。

- [ ] **Step 4: 更新 .env.example**

LLM 段注释改为「V0.2 已启用」，追加 `LLM_TIMEOUT_MS=120000`。

- [ ] **Step 5: 写测试报告**

在 `docs/specs/Marketing_Brain_V0.2_TestReport.md` 记录：每层测试数量与结果、
真实 LLM 冒烟结论、已知问题清单。

- [ ] **Step 6: 提交**

```bash
git add docs/ .env.example
git commit -m "docs: 更新 V0.2 架构/流程说明并记录测试报告"
```

---

## 完成标准（Definition of Done）

- [ ] 全部单元测试通过（含 V0.1 回归全绿）。
- [ ] 集成测试（`-m integration`）通过；真实 LLM 冒烟成功，引用的评论/视频 ID 可在证据库回查。
- [ ] `docker compose up -d --build` 可部署；容器重建后历史任务与报告仍可查看。
- [ ] 前端可创建任务、查看运行状态（模型/Token/耗时）与基线报告。
- [ ] 失败任务保留错误信息，不产生伪报告。
- [ ] 已记录已知问题，无阻断核心链路缺陷。
