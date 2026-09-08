"""工具 schema 与参数校验测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.agent.tools_spec import TOOL_JSON_SCHEMAS, TOOL_WHITELIST, validate_and_coerce, ToolSpecError


class TestToolSchema:
    def test_schema_covers_all_registered_tools(self):
        """schema 覆盖注册表全部 8 个工具。"""
        from app.analysis.registry import TOOL_REGISTRY
        names = {s["function"]["name"] for s in TOOL_JSON_SCHEMAS}
        assert names == set(TOOL_REGISTRY.keys())

    def test_whitelist_equals_registry(self):
        assert TOOL_WHITELIST == set(__import__(
            "app.analysis.registry", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY.keys())

    def test_schema_entries_are_function_objects(self):
        for s in TOOL_JSON_SCHEMAS:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]


class TestValidateAndCoerce:
    def test_empty_args_for_no_param_tool(self):
        assert validate_and_coerce({}, "data_coverage", 500) == {}

    def test_limit_coerced_into_range(self):
        # 默认 10，超上限钳到 max_records，低于 1 钳到 1
        # 未提供 limit 时使用默认值 10
        assert validate_and_coerce({}, "top_sources", 500) == {"limit": 10}
        assert validate_and_coerce({"limit": 9999}, "top_sources", 500) == {"limit": 500}
        assert validate_and_coerce({"limit": 0}, "top_sources", 500) == {"limit": 1}

    def test_sample_comments_defaults(self):
        out = validate_and_coerce({}, "sample_comments", 500)
        assert out["limit"] == 30
        assert out["keyword"] is None

    def test_drill_evidence_keeps_min_like(self):
        out = validate_and_coerce({"keyword": "油耗", "min_like": 20, "limit": 5}, "drill_evidence", 500)
        assert out == {"keyword": "油耗", "min_like": 20, "limit": 5}

    def test_object_compare_other_tags(self):
        out = validate_and_coerce({"other_tags": ["坦克300"]}, "object_compare", 500)
        assert out["other_tags"] == ["坦克300"]

    def test_unknown_tool_raises(self):
        with pytest.raises(ToolSpecError):
            validate_and_coerce({}, "nonexistent_tool", 500)

    def test_invalid_type_raises(self):
        with pytest.raises(ToolSpecError):
            validate_and_coerce({"limit": "abc"}, "top_sources", 500)
