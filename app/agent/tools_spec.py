"""工具 JSON Schema 与参数校验/钳制。

为 8 个确定性工具生成 OpenAI function 定义，供 LLMProvider.chat(tools=...) 使用。
在工具执行前将 LLM 传来的 arguments 校验、钳制为可直接传给工具函数的关键字参数，
杜绝越界 limit、未知工具、非法类型。仅此模块负责工具参数规范化。
"""
from typing import Any

from app.analysis.registry import TOOL_REGISTRY
from app.core.config import Settings


class ToolSpecError(Exception):
    """工具参数校验失败（未知工具 / 非法类型 / 超出允许范围）。"""


# 各工具参数规则。无参数工具值为 {"type": "object", "properties": {}}。
# limit 用 minimum=1；上限由 validate_and_coerce 用 max_records 钳制（不在 schema 写死，便于按请求收紧）。
_PROPERTY_RULES: dict[str, dict[str, Any]] = {
    "data_coverage": {},
    "volume_trend": {},
    "period_comparison": {},
    "topic_frequency_tool": {},
    "top_sources": {"limit": {"type": "integer", "minimum": 1, "default": 10}},
    "sample_comments": {
        "keyword": {"type": "string", "default": None},
        "limit": {"type": "integer", "minimum": 1, "default": 30},
    },
    "drill_evidence": {
        "keyword": {"type": "string", "default": None},
        "min_like": {"type": "integer", "minimum": 0, "default": None},
        "limit": {"type": "integer", "minimum": 1, "default": 30},
    },
    "object_compare": {"other_tags": {"type": "array", "items": {"type": "string"}, "default": None}},
}

# 各工具在参数缺省时的默认 limit（无 limit 参数的工具不在此列）。
_DEFAULT_LIMIT: dict[str, int] = {
    "top_sources": 10,
    "sample_comments": 30,
    "drill_evidence": 30,
}


def _to_openai_function(name: str, props: dict) -> dict:
    """把某工具的规则转成 OpenAI function 定义。"""
    if not props:
        return {
            "type": "function",
            "function": {"name": name, "description": name,
                         "parameters": {"type": "object", "properties": {}}},
        }
    properties = {}
    required = []
    for pname, rule in props.items():
        prop = {k: v for k, v in rule.items() if k != "default"}
        properties[pname] = prop
        if "default" not in rule:
            required.append(pname)
    return {
        "type": "function",
        "function": {"name": name, "description": name,
                     "parameters": {"type": "object", "properties": properties,
                                    "required": required}},
    }


TOOL_JSON_SCHEMAS: list[dict] = [
    _to_openai_function(name, _PROPERTY_RULES[name]) for name in TOOL_REGISTRY
]

TOOL_WHITELIST: set[str] = set(TOOL_REGISTRY.keys())


def validate_and_coerce(arguments: dict, tool_name: str, max_records: int) -> dict:
    """校验并规范化 LLM 传来的工具参数。

    Args:
        arguments: LLM 传来的参数字典（可为空或含非法字段）。
        tool_name: 工具名。
        max_records: 单次工具最大返回记录数（钳制 limit 上限）。

    Returns:
        可直接传给工具函数的关键字参数字典。

    Raises:
        ToolSpecError: 工具未注册，或参数类型非法。
    """
    if tool_name not in TOOL_REGISTRY:
        raise ToolSpecError(f"未知工具: {tool_name}")
    rules = _PROPERTY_RULES.get(tool_name, {})
    args = dict(arguments or {})

    out: dict[str, Any] = {}
    for pname, rule in rules.items():
        if pname == "limit":
            val = args.get("limit")
            if val is None:
                val = _DEFAULT_LIMIT.get(tool_name)
            if not isinstance(val, int) or isinstance(val, bool):
                raise ToolSpecError(f"tool '{tool_name}' 参数 limit 必须是整数，得到 {val!r}")
            val = max(1, min(int(val), int(max_records)))
            out["limit"] = val
            continue
        if "type" in rule:
            expected = rule["type"]
            if pname in args and args[pname] is not None:
                val = args[pname]
                if expected == "string" and not isinstance(val, str):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是字符串")
                if expected == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是整数")
                if expected == "array" and not isinstance(val, list):
                    raise ToolSpecError(f"tool '{tool_name}' 参数 {pname} 必须是数组")
                out[pname] = val
            else:
                out[pname] = None
    return out
