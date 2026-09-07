"""工作流引擎测试（用 mock 验证 stage loop 与工具授权拦截）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import pytest

from app.workflow.engine import (
    WorkflowEngine,
    ToolAuthorizationError,
    WorkflowExecutionError,
)
from app.skill.schema import SkillDefinition, StageDefinition
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from datetime import datetime


class FakeDS:
    """模拟 MySqlDataSource。"""
    def count_comments(self, **kw):
        return 100
    def time_series(self, **kw):
        return {"start": "2026-08-01", "end": "2026-08-31", "bucket": "day", "buckets": [], "total": 100}
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(
            comment_id=f"c{i}", content=f"评论{i}", video_title="坦克300",
            job_id="j1", comment_like_count=1, passed=True,
            is_car_owner=True, has_purchase_intent=False,
        ) for i in range(2)]


class FakeLLMProvider:
    """模拟 LLMProvider（不真实调用 API）。"""
    def __init__(self, response_override=None):
        self.response_override = response_override

    def chat(self, messages, **kwargs):
        if self.response_override:
            return self.response_override
        from app.llm.provider import LLMResult, LLMUsage
        return LLMResult(
            content=json.dumps({"ok": True}),
            usage=LLMUsage(model="test", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class TestWorkflowEngine:
    def _setup(self, tmp_path):
        """创建测试用临时数据库与组件。"""
        db_path = str(tmp_path / "test.db")

        task_repo = TaskRepository(db_path)
        task_repo.init_schema()

        event_repo = EventRepository(db_path)
        event_repo.init_schema()

        skill = SkillDefinition(
            version="0.3.0",
            name="test-skill",
            description="测试 Skill",
            stages=[
                StageDefinition(
                    name="snapshot",
                    system_prompt="确认范围",
                    tools=[],
                    output_schema={"type": "object", "required": ["ok"]},
                ),
                StageDefinition(
                    name="investigate",
                    system_prompt="调查分析",
                    tools=["data_coverage", "sample_comments"],
                    output_schema={"type": "object"},
                ),
            ],
        )

        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"]},
        )

        ds = FakeDS()
        store = EvidenceStore("test-task")
        llm = FakeLLMProvider()

        return task_repo, event_repo, skill, snap, ds, store, llm

    def test_workflow_executes_all_stages(self, tmp_path):
        """测试工作流按顺序执行所有 stages。"""
        task_repo, event_repo, skill, snap, ds, store, llm = self._setup(tmp_path)

        task = task_repo.create_task(
            raw_input="测试",
            parsed_intent={},
            snapshot=snap.to_dict(),
            skill_name="test-skill",
        )

        engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo,
                                stage_timeout=30.0)
        result = engine.run(task.task_id)

        # C2：报告取自最后一个 stage 的已验证输出（FakeLLM 返回 {"ok": true}）
        assert result["report"] == {"ok": True}

        # 验证事件记录
        events = event_repo.get_events(task.task_id)
        event_types = [e.event_type for e in events]
        assert "skill_selected" in event_types
        assert event_types.count("stage_start") == 2
        assert event_types.count("stage_done") == 2

    def test_unauthorized_tool_call_raises_error(self, tmp_path):
        """测试未授权工具调用抛出 ToolAuthorizationError。"""
        task_repo, event_repo, skill, snap, ds, store, llm = self._setup(tmp_path)

        task = task_repo.create_task(
            raw_input="测试",
            parsed_intent={},
            snapshot=snap.to_dict(),
            skill_name="test-skill",
        )

        engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo)

        # Patch _call_llm_for_stage 返回未授权工具
        original_call = engine._call_llm_for_stage
        def patched_call(stage):
            if stage.name == "investigate":
                return {
                    "content": json.dumps({"ok": True}),
                    "tool_calls": [{"name": "drill_evidence", "arguments": {}}],
                }
            return original_call(stage)
        engine._call_llm_for_stage = patched_call

        with pytest.raises(ToolAuthorizationError, match="unauthorized tool call"):
            engine.run(task.task_id)

        # 验证 tool_unauthorized 事件被记录
        events = event_repo.get_events(task.task_id)
        unauthorized_events = [e for e in events if e.event_type == "tool_unauthorized"]
        assert len(unauthorized_events) == 1
        assert unauthorized_events[0].payload["tool"] == "drill_evidence"

    def test_stage_output_schema_validation(self, tmp_path):
        """测试 Stage 输出 schema 校验失败抛出 WorkflowExecutionError。"""
        task_repo, event_repo, skill, snap, ds, store, llm = self._setup(tmp_path)

        task = task_repo.create_task(
            raw_input="测试",
            parsed_intent={},
            snapshot=snap.to_dict(),
            skill_name="test-skill",
        )

        engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo)

        # Patch _call_llm_for_stage 返回不符合 schema 的输出（snapshot stage 要求 "ok" 字段）
        original_call = engine._call_llm_for_stage
        def patched_call(stage):
            if stage.name == "snapshot":
                return {
                    "content": json.dumps({"wrong_field": True}),  # 缺少 "ok"
                    "tool_calls": [],
                }
            return original_call(stage)
        engine._call_llm_for_stage = patched_call

        with pytest.raises(WorkflowExecutionError, match="does not match schema"):
            engine.run(task.task_id)

    def test_assumption_and_judgment_are_registered(self, tmp_path):
        """I2：stage 输出含 assumptions/judgments 时登记并发射事件。"""
        task_repo, event_repo, skill, snap, ds, store, llm = self._setup(tmp_path)

        task = task_repo.create_task(
            raw_input="测试",
            parsed_intent={},
            snapshot=snap.to_dict(),
            skill_name="test-skill",
        )

        engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo)

        original_call = engine._call_llm_for_stage
        def patched_call(stage):
            if stage.name == "investigate":
                return {
                    "content": json.dumps({
                        "ok": True,
                        "judgments": [{"judgment_type": "risk", "title": "油耗争议"}],
                        "assumptions": [{"title": "样本有偏", "rationale": "仅抽样"}],
                    }),
                    "tool_calls": [],
                }
            return original_call(stage)
        engine._call_llm_for_stage = patched_call

        result = engine.run(task.task_id)

        # 判断与假设都已登记（judgment 无显式 evidence_refs 时默认引用当刻证据）
        kinds = [r.kind for r in store.all_records()]
        assert "judgment" in kinds
        assert "assumption" in kinds

        # 事件被发射
        events = event_repo.get_events(task.task_id)
        assert any(e.event_type == "judgment_made" for e in events)
        assert any(e.event_type == "assumption_added" for e in events)

        # report 取自最后一个 stage（investigate）的已验证输出
        assert result["report"]["ok"] is True
