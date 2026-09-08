"""V0.4 Agent API 集成测试（同步模式）。"""
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


class FakeLLM:
    def __init__(self):
        self.responses = []
    def push_json(self, content):
        self.responses.append({"content": content, "raw": {}})
    def _next(self):
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return nxt
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self._next()
        return json.loads(nxt["content"]), LLMResult(content=nxt["content"], usage=LLMUsage(model="m"))
    def chat(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self._next()
        return LLMResult(content=nxt.get("content"), usage=LLMUsage(model="m"), raw=nxt.get("raw", {}))


def _settings(**kw):
    from app.core.config import Settings
    base = dict(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                ENABLE_WORKFLOW_ENGINE=True, ENABLE_AGENT_ENGINE=True)
    base.update(kw)
    return Settings(**base)


def _app(tmp_path, llm, datasource, **kw):
    from app.api.routes import create_app
    settings = _settings(**kw)
    return create_app(
        db_path=os.path.join(tmp_path, "app_state.db"),
        datasource=datasource, settings_override=settings, llm_provider=llm,
        background=True, require_sync=True,
    )


def test_create_task_uses_agent_when_enabled(tmp_path):
    llm = FakeLLM()
    # Supervisor 卡
    llm.push_json(json.dumps({"cards": [{"card_id": "c1", "goal_type": "pulse", "title": "t",
                                          "objective": "o", "evidence_requirements": [],
                                          "suggested_tools": [], "priority": 1}]}))
    # Investigator stop（不实际调用工具）
    llm.push_json(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient",
                               "summary": "s", "findings": ["f"]}))
    # Reviewer pass
    llm.push_json(json.dumps({"verdict": "pass", "issues": []}))

    app = _app(tmp_path, llm, FakeData(), ENABLE_AGENT_ENGINE=True, ENABLE_WORKFLOW_ENGINE=True)
    client = TestClient(app)
    resp = client.post("/api/tasks", json={"raw_input": "分析坦克300"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "agent" in (data.get("result") or {})


def test_agent_disabled_falls_back_to_v02(tmp_path):
    llm = FakeLLM()
    # V0.2 报告输出
    llm.push_json(json.dumps({"scope": {"comment_count": 100}, "themes": []}))

    app = _app(tmp_path, llm, FakeData(), ENABLE_AGENT_ENGINE=False, ENABLE_WORKFLOW_ENGINE=False)
    client = TestClient(app)
    resp = client.post("/api/tasks", json={"raw_input": "分析坦克300"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["skill_name"] is None


def test_agent_passthrough_max_params(tmp_path):
    llm = FakeLLM()
    llm.push_json(json.dumps({"cards": [{"card_id": "c1", "goal_type": "pulse", "title": "t",
                                          "objective": "o", "evidence_requirements": [],
                                          "suggested_tools": [], "priority": 1}]}))
    llm.push_json(json.dumps({"type": "stop", "stop_reason": "evidence_sufficient",
                               "summary": "s", "findings": ["f"]}))
    llm.push_json(json.dumps({"verdict": "pass", "issues": []}))

    app = _app(tmp_path, llm, FakeData(), ENABLE_AGENT_ENGINE=True, ENABLE_WORKFLOW_ENGINE=True)
    client = TestClient(app)
    resp = client.post("/api/tasks", json={"raw_input": "分析坦克300", "max_subtasks": 1, "max_tool_calls": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "agent" in (data.get("result") or {})
