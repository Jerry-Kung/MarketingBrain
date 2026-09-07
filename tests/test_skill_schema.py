"""Skill schema 测试（序列化/反序列化）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.skill.schema import StageDefinition, SkillDefinition


class TestStageDefinition:
    def test_from_dict_and_to_dict(self):
        data = {
            "name": "investigate",
            "system_prompt": "你是调查员",
            "tools": ["data_coverage", "sample_comments"],
            "output_schema": {"type": "object", "required": ["findings"]},
        }
        stage = StageDefinition.from_dict(data)
        assert stage.name == "investigate"
        assert stage.system_prompt == "你是调查员"
        assert stage.tools == ["data_coverage", "sample_comments"]
        assert stage.output_schema == {"type": "object", "required": ["findings"]}
        assert stage.to_dict() == data


class TestSkillDefinition:
    def test_from_dict_and_to_dict(self):
        data = {
            "version": "0.3.0",
            "name": "test-skill",
            "description": "测试 Skill",
            "stages": [
                {
                    "name": "snapshot",
                    "system_prompt": "确认范围",
                    "tools": [],
                    "output_schema": {"type": "object"},
                },
                {
                    "name": "investigate",
                    "system_prompt": "调查分析",
                    "tools": ["data_coverage"],
                    "output_schema": {"type": "object"},
                },
            ],
        }
        skill = SkillDefinition.from_dict(data)
        assert skill.version == "0.3.0"
        assert skill.name == "test-skill"
        assert skill.description == "测试 Skill"
        assert len(skill.stages) == 2
        assert skill.stages[0].name == "snapshot"
        assert skill.stages[1].tools == ["data_coverage"]
        assert skill.to_dict() == data
