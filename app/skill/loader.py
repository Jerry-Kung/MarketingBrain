"""Skill YAML 加载器。"""
from pathlib import Path

import yaml

from app.skill.schema import SkillDefinition


class SkillNotFoundError(Exception):
    """Skill 文件不存在。"""


class SkillVersionError(Exception):
    """Skill 版本不匹配。"""


class SkillLoader:
    """从 YAML 文件加载 Skill 定义并缓存。"""

    def __init__(self, skills_dir: Path | str = Path("skills")):
        self.skills_dir = Path(skills_dir)
        self._cache: dict[str, SkillDefinition] = {}

    def load(self, skill_name: str) -> SkillDefinition:
        """加载并验证 Skill YAML，缓存结果。

        Args:
            skill_name: Skill 名称（不含 .yaml 后缀）

        Returns:
            SkillDefinition 实例

        Raises:
            SkillNotFoundError: Skill 文件不存在
            SkillVersionError: Skill 版本不是 0.3.0
        """
        if skill_name in self._cache:
            return self._cache[skill_name]

        skill_path = self.skills_dir / f"{skill_name}.yaml"
        if not skill_path.exists():
            raise SkillNotFoundError(
                f"Skill '{skill_name}' not found at {skill_path}"
            )

        with open(skill_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 版本校验：V0.3 只接受 "0.3.0"
        version = data.get("version")
        if version != "0.3.0":
            raise SkillVersionError(
                f"Skill '{skill_name}' version mismatch: expected '0.3.0', got '{version}'"
            )

        skill = SkillDefinition.from_dict(data)
        self._cache[skill_name] = skill
        return skill
