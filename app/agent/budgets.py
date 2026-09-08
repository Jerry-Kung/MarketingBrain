"""Agent 预算控制（硬限制）。

预算为每次任务的硬上限：超过即停止，绝不静默解锁。BudgetCounter 负责累计，
exhausted() 判定任一维度超限。上限来自配置或请求级覆盖。
"""
from dataclasses import dataclass


class BudgetExhaustedError(Exception):
    """预算已耗尽（任一维度超出硬上限）。"""


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
    """运行时累计预算使用，并判定是否超限。

    - subtasks / tool_calls / supplements：任务级全局累计（跨卡片总计）。
    - loops：单卡循环上限（文档语义）。card_loops_used 为当前卡片的循环计数，
      每次 begin_card() 时归零；loop_exceeded() 仅依据当前卡片计数判定。
      跨卡累计的 loops_used 仍保留，用于预算报告展示。
    """

    def __init__(self, budget: AgentBudget):
        self.budget = budget
        self.subtasks_used = 0
        self.loops_used = 0
        self.tool_calls_used = 0
        self.supplements_used = 0
        self.card_loops_used = 0

    def begin_card(self) -> None:
        """进入一张新调查卡时调用：重置单卡循环计数。

        单卡循环预算（loops）按卡独立，避免前一张卡耗尽循环预算后
        后续卡片一进循环即被判预算耗尽。
        """
        self.card_loops_used = 0

    def record_subtask(self) -> int:
        self.subtasks_used += 1
        return self.subtasks_used

    def record_loop(self) -> int:
        self.loops_used += 1
        self.card_loops_used += 1
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
        return self.card_loops_used > self.budget.loops

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
