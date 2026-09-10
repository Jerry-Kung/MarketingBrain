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

    def test_stop_json_wrapped_in_prose(self):
        """回归：真实 LLM 把 stop JSON 用散文+```json 围栏包裹，必须能解析。

        此前 _parse_turn 只做严格 json.loads(content)，遇到"散文 + 代码围栏包裹
        的 JSON"直接判非法，导致几乎所有子任务在 2-4 次工具调用后报
        "LLM 输出非法，停止"。容错提取后此类输出应正常收敛为 evidence_sufficient。
        """
        prose_wrapped = (
            "基于已获取的声量趋势数据和评论证据，我已完成分析。结论如下：\n\n"
            "```json\n"
            '{\n  "type": "stop",\n  "stop_reason": "evidence_sufficient",\n'
            '  "summary": "声量高峰集中在8月25-26日",\n'
            '  "findings": [{"node": "2026-08-26", "count": 7358, '
            '"event": "成都车展猛士X700首秀"}],\n'
            '  "hypothesis": "声量由车展驱动"\n}\n'
            "```"
        )
        llm = FakeLLM([
            {"content": None, "raw": {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "sample_comments", "arguments": '{"limit": 5}'}}
            ]}}]}},
            {"content": prose_wrapped},
        ])
        inv, event_repo, store, counter = _setup(llm)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "evidence_sufficient"
        # findings 为对象数组时应被规范为字符串列表
        assert isinstance(result.findings, list)
        assert all(isinstance(f, str) for f in result.findings)
        assert result.findings and "成都车展猛士X700首秀" in result.findings[0]

    def test_stop_json_in_dict_findings_coerced_to_str(self):
        """回归：findings 为对象数组时规范为字符串列表，避免 Orchestrator 组装异常。"""
        llm = FakeLLM([
            {"content": json.dumps({
                "type": "stop", "stop_reason": "evidence_sufficient", "summary": "ok",
                "findings": [{"event": "车展首秀", "node": "26日"}, {"title": "峰值"}],
            })},
        ])
        inv, event_repo, store, counter = _setup(llm)
        result = inv.run(_card(), "t1", 1)
        assert result.stop_reason == "evidence_sufficient"
        assert all(isinstance(f, str) for f in result.findings)
        assert "车展首秀" in result.findings[0]

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
        # 抽样类工具的「抽到几条」用独立键 sampled_count，不与 data_coverage 的
        # 「窗口总量」comment_count 同名（一键两义会让审计读者误判口径）。
        assert payload["result_summary"].get("sampled_count") == 5
        assert "comment_count" not in payload["result_summary"],             "抽样工具不应写 comment_count（该键专指窗口总量）"
        # 工具级 sample_size 必须保留（计划原意：样本量与工具 sample_size 一致）
        assert payload["result_summary"].get("sample_size") == 5
        assert payload["sample_size"] == 5
        assert "bias_note" in payload, "subtask_tool 应记录 bias_note"

    def test_result_summary_keeps_object_compare_numbers(self):
        """回归：object_compare 的真实返回键必须进入 result_summary。

        工具返回 {"object_count","other_count","compared","other_tags"}，
        _take 的键清单此前只覆盖 current/previous/change_rate，导致 object_compare
        的摘要退化成只有 sample_size，审计上看不到对比数字。
        """
        from app.agent.investigator import _result_summary
        res = {
            "name": "object_compare", "sample_size": 100,
            "result": {"object_count": 100, "other_count": 40,
                       "compared": True, "other_tags": ["坦克500"]},
        }
        summary = _result_summary(res)
        assert summary["object_count"] == 100
        assert summary["other_count"] == 40
        assert summary["compared"] is True
        assert summary["other_tags"] == ["坦克500"]

    def test_result_summary_drops_non_scalar_values(self):
        """标量守卫：未来工具的大型结构化值不得绕过体量控制进入事件。"""
        from app.agent.investigator import _result_summary
        res = {"name": "x", "sample_size": 3,
               "result": {"total": 7, "current": {"nested": list(range(1000))}}}
        summary = _result_summary(res)
        assert summary["total"] == 7
        assert "current" not in summary
