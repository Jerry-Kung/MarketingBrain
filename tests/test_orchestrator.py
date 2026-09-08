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
