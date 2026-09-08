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

    def test_priority_accepts_semantic_label(self):
        # 真实 LLM 可能输出 priority 为语义标签而非整数，应容错归一化而非抛 ValueError
        for label, expect in [("high", 3), ("medium", 2), ("low", 1), ("urgent", 3), ("normal", 2)]:
            card = InvestigationCard.from_dict({"card_id": "c1", "objective": "o", "priority": label})
            assert card.priority == expect

    def test_priority_accepts_numeric_string(self):
        card = InvestigationCard.from_dict({"card_id": "c1", "objective": "o", "priority": "2"})
        assert card.priority == 2

    def test_priority_defaults_on_garbage(self):
        # 无法解析的怪异值回退默认值 1，不让任务因 LLM 输出边界而失败
        card = InvestigationCard.from_dict({"card_id": "c1", "objective": "o", "priority": "abc"})
        assert card.priority == 1


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
