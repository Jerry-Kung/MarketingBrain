"""工作流上下文构建测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflow.context import build_stage_context
from app.skill.schema import StageDefinition
from app.store.evidence import EvidenceStore
from app.datasource.models import CommentRecord


def _comment(cid="c1"):
    return CommentRecord(
        comment_id=cid, content="测试评论", video_title="坦克300",
        job_id="j1", comment_like_count=5, passed=True,
        is_car_owner=True, has_purchase_intent=False,
    )


class TestBuildStageContext:
    def test_empty_evidence(self):
        """测试无证据时仅返回 stage system_prompt。"""
        stage = StageDefinition(
            name="snapshot",
            system_prompt="你是数据边界确认专员。",
            tools=[],
            output_schema={},
        )
        store = EvidenceStore(task_id="t1")

        messages = build_stage_context(stage, store)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert "数据边界确认专员" in messages[0]["content"]
        assert "已有证据" not in messages[0]["content"]

    def test_with_evidence(self):
        """测试有证据时追加证据摘要。"""
        stage = StageDefinition(
            name="investigate",
            system_prompt="你是调查分析师。",
            tools=["data_coverage"],
            output_schema={},
        )
        store = EvidenceStore(task_id="t1")
        store.register_comment(_comment(cid="c1"), source="sample")
        store.register_comment(_comment(cid="c2"), source="sample")
        store.register_stat(label="total", detail="总数", count=100, source="coverage")

        messages = build_stage_context(stage, store)
        assert len(messages) == 1
        content = messages[0]["content"]
        assert "调查分析师" in content
        assert "已有证据" in content
        assert "评论证据: 2 条" in content
        assert "统计证据: 1 条" in content
