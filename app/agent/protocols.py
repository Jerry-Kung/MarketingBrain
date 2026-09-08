"""V0.4 Agent 协议数据结构。

调查卡（InvestigationCard）、子 Agent 回合输出（InvestigatorResult）、
评审结论（ReviewVerdict）与停止原因常量。所有结构可由 dict 序列化，便于事件记录与前端展示。
子 Agent 的回合（turn）以普通 dict 表示（{"type": "tool_call"|"stop", ...}），
配合 InvestigatorResult 使用，不单独定义 InvestigatorTurn 数据类型。
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
