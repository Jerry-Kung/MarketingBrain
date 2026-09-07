"""工具注册表（工具名 → 可调用对象映射）。

WorkflowEngine 在运行时通过工具名查找并调用工具。V0.3 的 8 个确定性工具
全部注册在此。未来新增工具需同步更新此注册表。
"""
from typing import Callable

from app.analysis import tools


class ToolNotFoundError(Exception):
    """工具未注册。"""


TOOL_REGISTRY: dict[str, Callable] = {
    "data_coverage": tools.data_coverage,
    "volume_trend": tools.volume_trend,
    "period_comparison": tools.period_comparison,
    "topic_frequency_tool": tools.topic_frequency_tool,
    "top_sources": tools.top_sources,
    "sample_comments": tools.sample_comments,
    "drill_evidence": tools.drill_evidence,
    "object_compare": tools.object_compare,
}


def get_tool(name: str) -> Callable:
    """根据工具名查找工具函数。

    Args:
        name: 工具名（如 "data_coverage"）

    Returns:
        工具函数（Callable）

    Raises:
        ToolNotFoundError: 工具未注册
    """
    if name not in TOOL_REGISTRY:
        raise ToolNotFoundError(
            f"Tool '{name}' not registered. Available tools: {', '.join(TOOL_REGISTRY.keys())}"
        )
    return TOOL_REGISTRY[name]
