"""预算控制测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.agent.budgets import AgentBudget, BudgetCounter


class TestAgentBudget:
    def test_from_settings(self):
        from app.core.config import Settings
        s = Settings(
            DB_HOST="localhost", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
            LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
            AGENT_MAX_SUBTASKS=4, AGENT_MAX_LOOPS=2, AGENT_MAX_TOOL_CALLS=8, AGENT_MAX_SUPPLEMENTS=1,
        )
        b = AgentBudget.from_settings(s)
        assert b.subtasks == 4 and b.loops == 2 and b.tool_calls == 8 and b.supplements == 1


class TestBudgetCounter:
    def _counter(self):
        return BudgetCounter(AgentBudget(subtasks=2, loops=2, tool_calls=3, supplements=1))

    def test_counts_and_bounds(self):
        c = self._counter()
        assert c.tool_calls_exceeded() is False
        c.record_tool_call(); c.record_tool_call(); c.record_tool_call()
        assert c.tool_calls_used == 3
        assert c.tool_calls_exceeded() is False  # == max 仍未超
        c.record_tool_call()
        assert c.tool_calls_used == 4
        assert c.tool_calls_exceeded() is True

    def test_subtask_and_loop_bounds(self):
        c = self._counter()
        c.record_subtask(); c.record_subtask()
        assert c.subtask_exceeded() is False
        c.record_subtask()
        assert c.subtask_exceeded() is True

        c.record_loop(); c.record_loop()
        assert c.loop_exceeded() is False
        c.record_loop()
        assert c.loop_exceeded() is True

    def test_supplement_bound(self):
        c = self._counter()
        c.record_supplement()
        assert c.supplement_exceeded() is False
        c.record_supplement()
        assert c.supplement_exceeded() is True

    def test_exhausted_and_to_dict(self):
        c = self._counter()
        c.record_tool_call(); c.record_tool_call(); c.record_tool_call()
        c.record_tool_call()
        assert c.exhausted() is True
        d = c.to_dict()
        assert d["tool_calls"]["used"] == 4
        assert d["tool_calls"]["limit"] == 3
