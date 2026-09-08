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
