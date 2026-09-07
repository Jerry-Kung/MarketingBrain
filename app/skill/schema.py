"""Skill 定义数据结构。"""
from dataclasses import dataclass


@dataclass
class StageDefinition:
    """工作流阶段定义。"""

    name: str
    system_prompt: str
    tools: list[str]
    output_schema: dict

    @classmethod
    def from_dict(cls, data: dict) -> "StageDefinition":
        return cls(
            name=data["name"],
            system_prompt=data["system_prompt"],
            tools=data.get("tools", []),
            output_schema=data.get("output_schema", {}),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "output_schema": self.output_schema,
        }


@dataclass
class SkillDefinition:
    """Skill 定义（YAML 加载后的结构化表示）。"""

    version: str
    name: str
    description: str
    stages: list[StageDefinition]

    @classmethod
    def from_dict(cls, data: dict) -> "SkillDefinition":
        stages = [StageDefinition.from_dict(s) for s in data.get("stages", [])]
        return cls(
            version=data["version"],
            name=data["name"],
            description=data.get("description", ""),
            stages=stages,
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "stages": [s.to_dict() for s in self.stages],
        }
