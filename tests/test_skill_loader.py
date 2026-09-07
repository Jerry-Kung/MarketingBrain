"""Skill loader 测试（加载、版本校验、缓存）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.skill.loader import SkillLoader, SkillNotFoundError, SkillVersionError


class TestSkillLoader:
    def test_load_existing_skill(self):
        loader = SkillLoader(skills_dir=Path("skills"))
        skill = loader.load("test-skill-v03")
        assert skill.version == "0.3.0"
        assert skill.name == "test-skill-v03"
        assert len(skill.stages) == 2
        assert skill.stages[0].name == "snapshot"
        assert skill.stages[1].tools == ["data_coverage", "sample_comments"]

    def test_load_caches_skill(self):
        loader = SkillLoader(skills_dir=Path("skills"))
        skill1 = loader.load("test-skill-v03")
        skill2 = loader.load("test-skill-v03")
        assert skill1 is skill2  # 同一对象引用

    def test_skill_not_found(self):
        loader = SkillLoader(skills_dir=Path("skills"))
        with pytest.raises(SkillNotFoundError, match="not found"):
            loader.load("nonexistent-skill")

    def test_skill_version_mismatch(self):
        """测试版本不匹配（需手动创建一个 version: "0.2.0" 的测试文件）。"""
        # 创建临时 Skill 文件用于测试
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            skill_path = tmpdir_path / "old-skill.yaml"
            skill_path.write_text(
                yaml.dump({
                    "version": "0.2.0",
                    "name": "old-skill",
                    "description": "旧版本",
                    "stages": [],
                }),
                encoding="utf-8",
            )
            loader = SkillLoader(skills_dir=tmpdir_path)
            with pytest.raises(SkillVersionError, match="version mismatch"):
                loader.load("old-skill")
