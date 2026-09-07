"""V0.3 真实 LLM 冒烟测试（需真实 LLM API，手动执行）。

标记为 manual，不在 CI 中自动运行。需手动执行：
pytest tests/test_v03_smoke.py -v -m manual
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import tempfile
from datetime import datetime

from app.core.config import Settings
from app.skill.loader import SkillLoader
from app.workflow.engine import WorkflowEngine
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.llm.provider import LLMProvider
from app.datasource.adapter import MySqlDataSource


@pytest.mark.manual
def test_real_llm_opinion_pulse():
    """真实 LLM + 真实 MySQL 端到端冒烟测试。"""
    # 从 .env 加载配置
    settings = Settings()

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = db_file.name
    db_file.close()

    task_repo = TaskRepository(db_path)
    task_repo.init_schema()
    event_repo = EventRepository(db_path)
    event_repo.init_schema()

    loader = SkillLoader(skills_dir=Path("skills"))
    skill = loader.load("opinion-pulse")

    snap = LogicalSnapshot(
        start_time=datetime(2026, 8, 1),
        end_time=datetime(2026, 8, 31),
        extra={"video_tags": ["坦克300"]},
    )

    task = task_repo.create_task(
        raw_input="分析坦克300近期舆情",
        parsed_intent={"goal_type": "pulse"},
        snapshot=snap.to_dict(),
        skill_name="opinion-pulse",
    )

    # 真实 datasource 和 llm_provider
    ds = MySqlDataSource.from_settings(settings)
    store = EvidenceStore(task.task_id)
    llm = LLMProvider.from_settings(settings)

    engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo)

    # Execute
    result = engine.run(task.task_id)

    # Verify
    assert result is not None
    assert "report" in result
    assert len(result["evidence"]) > 0

    # 验证 Skill 正确加载
    assert skill.name == "opinion-pulse"
    assert len(skill.stages) == 3

    # 验证各 stage 按顺序执行
    events = event_repo.get_events(task.task_id)
    event_types = [e.event_type for e in events]
    assert "skill_selected" in event_types
    assert event_types.count("stage_start") == 3
    assert event_types.count("stage_done") == 3

    # 验证工具调用成功并记录事件
    tool_calls = [e for e in events if e.event_type == "tool_call"]
    assert len(tool_calls) > 0

    # 验证报告生成且证据引用有效
    all_records = store.all_records()
    assert len(all_records) > 0

    print(f"✓ 真实 LLM 冒烟测试通过")
    print(f"  - Skill: {skill.name}")
    print(f"  - Stages 执行: {event_types.count('stage_done')}")
    print(f"  - 工具调用: {len(tool_calls)}")
    print(f"  - 证据登记: {len(all_records)}")
