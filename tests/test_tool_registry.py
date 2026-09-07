"""工具注册表测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.analysis.registry import TOOL_REGISTRY, get_tool, ToolNotFoundError
from app.analysis import tools


class TestToolRegistry:
    def test_registry_contains_8_tools(self):
        """验证注册表包含全部 8 个工具。"""
        assert len(TOOL_REGISTRY) == 8
        expected_tools = [
            "data_coverage",
            "volume_trend",
            "period_comparison",
            "topic_frequency_tool",
            "top_sources",
            "sample_comments",
            "drill_evidence",
            "object_compare",
        ]
        for name in expected_tools:
            assert name in TOOL_REGISTRY

    def test_get_tool_returns_callable(self):
        """验证 get_tool 返回可调用对象。"""
        tool = get_tool("data_coverage")
        assert callable(tool)
        assert tool is tools.data_coverage

    def test_get_tool_not_found(self):
        """验证未注册工具抛出 ToolNotFoundError。"""
        with pytest.raises(ToolNotFoundError, match="not registered"):
            get_tool("nonexistent_tool")

    def test_all_registered_tools_are_callable(self):
        """验证注册表中的所有工具都是可调用对象。"""
        for name, fn in TOOL_REGISTRY.items():
            assert callable(fn), f"Tool '{name}' is not callable"
