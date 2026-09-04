"""报告生成测试（用 MockLLMProvider 返回固定 JSON，不连真实 API）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.llm.report import build_report_messages, generate_strategy_pack, REPORT_SYSTEM_PROMPT
from app.pipeline.baseline import AnalysisBundle
from app.llm.provider import LLMResult, LLMUsage


class MockProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat_json(self, messages, **kw):
        self.calls.append(messages)
        self.kwargs = kw
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
        stats=[
            {
                "name": "data_coverage",
                "params": {"foo": "bar"},
                "result": {"comment_count": 100},
                "evidence_ids": ["c1"],
                "sample_size": 100,
                "time_range": {"start": "x", "end": "y"},
                "bias_note": "样本有限",
            },
        ],
    )


class TestReportGen:
    def test_build_messages_includes_bundle(self):
        intent = {"object": "坦克300", "goal_type": "pulse"}
        msgs = build_report_messages(_bundle(), intent)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "坦克300" in msgs[1]["content"]

    def test_system_prompt_lists_schema_keys(self):
        for key in ("scope", "overall", "themes", "sources", "risk_opportunity",
                    "evidence_gaps", "assumptions", "actions", "metrics"):
            assert key in REPORT_SYSTEM_PROMPT

    def test_system_prompt_forbids_inventing_ids(self):
        assert "comment_id" in REPORT_SYSTEM_PROMPT
        assert "job_id" in REPORT_SYSTEM_PROMPT

    def test_build_messages_trims_stats_payload(self):
        msgs = build_report_messages(_bundle(), {})
        ctx = json.loads(msgs[1]["content"].split("\n\n", 1)[1])
        stat = ctx["stats"][0]
        assert set(stat.keys()) == {"name", "result", "sample_size", "bias_note"}
        assert stat["name"] == "data_coverage"
        assert stat["result"] == {"comment_count": 100}

    def test_build_messages_includes_core_sections(self):
        msgs = build_report_messages(_bundle(), {})
        ctx = json.loads(msgs[1]["content"].split("\n\n", 1)[1])
        for key in ("intent", "scope", "overall", "themes", "sources", "samples", "stats"):
            assert key in ctx

    def test_generate_strategy_pack_parses(self):
        payload = {"scope": {"comment_count": 100}, "themes": []}
        provider = MockProvider(payload)
        report, result = generate_strategy_pack(_bundle(), provider)
        assert report["scope"]["comment_count"] == 100
        assert result.usage.total_tokens == 15

    def test_generate_strategy_pack_uses_max_tokens(self):
        provider = MockProvider({"scope": {}})
        generate_strategy_pack(_bundle(), provider)
        assert provider.kwargs.get("max_tokens") == 4000

    def test_generate_strategy_pack_forwards_intent(self):
        """Controller ruling P1: intent 必须透传给 build_report_messages。"""
        payload = {"scope": {"comment_count": 100}, "themes": []}
        provider = MockProvider(payload)
        intent = {"object": "坦克500", "goal_type": "pulse"}
        generate_strategy_pack(_bundle(), provider, intent=intent)
        sent_messages = provider.calls[0]
        assert "坦克500" in sent_messages[1]["content"]

    def test_generate_strategy_pack_default_intent_is_empty(self):
        payload = {"scope": {}}
        provider = MockProvider(payload)
        generate_strategy_pack(_bundle(), provider)
        sent_messages = provider.calls[0]
        ctx = json.loads(sent_messages[1]["content"].split("\n\n", 1)[1])
        assert ctx["intent"] == {}
