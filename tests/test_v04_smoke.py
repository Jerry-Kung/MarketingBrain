"""V0.4 真实 LLM 冒烟测试（需真实 LLM API + 真实 MySQL，手动执行）。

pytest tests/test_v04_smoke.py -v -m manual

标记为 manual，默认被 pytest.ini 的 `-m "not manual"` 排除，不进入常规回归。
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from datetime import datetime

from app.core.config import load_settings
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.understanding.intent import AnalysisIntent
from app.llm.provider import LLMProvider
from app.datasource.adapter import MySqlDataSource


@pytest.mark.manual
def test_real_llm_agent_dynamic_drill():
    settings = load_settings()
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    task_repo = TaskRepository(db.name)
    task_repo.init_schema()
    event_repo = EventRepository(db.name)
    event_repo.init_schema()
    store = EvidenceStore("manual")
    snap = LogicalSnapshot(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        extra={"video_tags": ["坦克300"]},
    )
    ds = MySqlDataSource.from_settings(settings)
    llm = LLMProvider.from_settings(settings)

    from app.agent.orchestrator import Orchestrator
    task = task_repo.create_task(
        raw_input="分析坦克300近期舆情",
        parsed_intent={"goal_type": "pulse"},
        snapshot=snap.to_dict(),
    )
    orch = Orchestrator(llm, ds, snap, store, event_repo, task_repo, settings)
    result = orch.run(task.task_id, AnalysisIntent("坦克300", None, "pulse"))
    assert "report" in result
    assert "agent" in result
    assert isinstance(result["agent"]["budget"], dict)
    tools = [
        e.payload["tool"]
        for e in event_repo.get_events(task.task_id)
        if e.event_type == "subtask_tool"
    ]
    print(f"✓ 真实 LLM 冒烟通过；工具调用序列: {tools}")
