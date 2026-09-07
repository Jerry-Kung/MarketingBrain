"""V0.3 工作流集成测试（真实 datasource + mock LLM）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from datetime import datetime

from app.skill.loader import SkillLoader
from app.workflow.engine import WorkflowEngine
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot


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
        ) for i in range(limit)]
    def top_videos(self, **kw):
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 20}]
    def topic_frequency(self, **kw):
        return [{"topic": "油耗", "comment_count": 40}]


class FakeLLMProvider:
    """模拟 LLMProvider（按 stage 返回符合其 output_schema 的 JSON）。

    合成 stage 的判断引用"已登记证据"（evidence_refs），以验证证据引用图完整性。
    """
    def __init__(self, store=None):
        self.store = store

    def chat(self, messages, **kwargs):
        from app.llm.provider import LLMResult, LLMUsage
        import re
        system_prompt = messages[0]["content"] if messages else ""
        if "数据边界确认" in system_prompt:
            content = {"scope_confirmed": True}
        elif "舆情调查分析" in system_prompt:
            content = {"findings": ["发现1", "发现2"]}
        else:
            # synthesize：判断引用当前已登记的证据 ID，保证引用图非空
            refs = [e.evidence_id for e in self.store.all_records()] if self.store else []
            content = {
                "report": {"risks": [], "opportunities": [], "recommendations": []},
                "judgments": [
                    {"judgment_type": "risk", "title": "油耗争议", "evidence_refs": refs},
                ],
            }
        return LLMResult(
            content=json.dumps(content),
            usage=LLMUsage(model="test", prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class TestWorkflowIntegration:
    def _build_engine(self, tmp_path):
        """构造共享组件并返回 (engine, task, store, event_repo, task_repo)。"""
        db_path = str(tmp_path / "test.db")

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

        ds = FakeDS()
        store = EvidenceStore(task.task_id)
        llm = FakeLLMProvider(store=store)

        engine = WorkflowEngine(skill, ds, snap, store, llm, task_repo, event_repo)
        return engine, task, store, event_repo

    def test_opinion_pulse_full_workflow(self, tmp_path):
        """测试 opinion-pulse Skill 完整工作流。"""
        engine, task, store, event_repo = self._build_engine(tmp_path)

        # Execute
        result = engine.run(task.task_id)

        # Verify
        # C2：报告取自 synthesize stage 的已验证输出（含 "report" 键）
        assert result["report"]["risks"] == []
        assert len(result["evidence"]) > 0  # 应有证据登记

        # 验证事件记录
        events = event_repo.get_events(task.task_id)
        event_types = [e.event_type for e in events]
        assert "skill_selected" in event_types
        assert event_types.count("stage_start") == 3  # opinion-pulse 有 3 个 stages
        assert event_types.count("stage_done") == 3
        assert "tool_call" in event_types  # investigate stage 应有工具调用

    def test_evidence_reference_graph_integrity(self, tmp_path):
        """测试证据引用图完整性（synthesize 判断引用真实已登记证据）。"""
        engine, task, store, event_repo = self._build_engine(tmp_path)
        result = engine.run(task.task_id)

        # 验证所有 judgment 引用的 evidence_id 都存在于 store
        all_records = store.all_records()
        evidence_ids = {r.evidence_id for r in all_records}

        judgments = [r for r in all_records if r.kind == "judgment"]
        # I2：judgments 不应为空（synthesize 阶段登记了判断）
        assert len(judgments) >= 1, "应至少登记一个 judgment（synthesize 阶段）"
        for j in judgments:
            refs = j.extra.get("evidence_refs", [])
            assert len(refs) >= 1, f"Judgment {j.evidence_id} 应引用至少一条证据"
            for ref_id in refs:
                assert ref_id in evidence_ids, f"Judgment {j.evidence_id} 引用不存在的证据 {ref_id}"

        # 验证 judgment_made 事件被发射
        events = event_repo.get_events(task.task_id)
        assert any(e.event_type == "judgment_made" for e in events)
