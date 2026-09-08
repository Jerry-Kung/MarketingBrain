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
