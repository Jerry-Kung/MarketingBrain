"""证据索引测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.store.evidence import EvidenceStore
from app.datasource.models import CommentRecord


def _comment(cid="c1", job="j1", content="好车", vt="坦克300"):
    return CommentRecord(
        comment_id=cid, content=content, video_title=vt, job_id=job,
        comment_like_count=7, passed=True, is_car_owner=True,
        has_purchase_intent=False,
    )


class TestEvidenceStore:
    def test_register_and_has_comment(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_comment(_comment(), source="sample")
        assert store.has_comment("c1")
        assert rec.evidence_id
        assert rec.kind == "comment"

    def test_register_video(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="top")
        assert store.has_video("j1")
        assert rec.kind == "video"

    def test_register_stat(self):
        store = EvidenceStore(task_id="t1")
        rec = store.register_stat(label="total", detail="n", count=100, source="count")
        assert rec.kind == "stat"

    def test_to_dicts_roundtrip(self):
        store = EvidenceStore(task_id="t1")
        store.register_comment(_comment(), source="sample")
        store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="top")
        dicts = store.to_dicts()
        assert len(dicts) == 2
        assert all("evidence_id" in d and "kind" in d for d in dicts)

    def test_partial_flags_none(self):
        """测试 None 值和 False 值正确透传。"""
        store = EvidenceStore(task_id="t1")
        # 注册带有 None 标志和 passed=False 的评论
        rec = store.register_comment(
            CommentRecord(
                comment_id="c_none",
                content="test",
                video_title="test_video",
                job_id="j1",
                is_car_owner=None,
                has_purchase_intent=None,
                passed=False,
            ),
            source="sample",
        )
        # 验证 EvidenceRecord 中的值
        assert rec.is_car_owner is None
        assert rec.has_purchase_intent is None
        assert rec.passed is False

        # 验证 to_dict() 中的值
        d = rec.to_dict()
        assert d["is_car_owner"] is None
        assert d["has_purchase_intent"] is None
        assert d["passed"] is False

        # 验证 all_records() 中的值
        recs = store.all_records()
        assert recs[0].is_car_owner is None
        assert recs[0].has_purchase_intent is None
        assert recs[0].passed is False

        # 保证 True 值也能正确透传
        rec2 = store.register_comment(_comment(), source="sample2")
        assert rec2.is_car_owner is True
        assert rec2.passed is True

    def test_register_comment_idempotent(self):
        """测试注册相同评论 ID 返回相同 evidence_id（幂等）。"""
        store = EvidenceStore(task_id="t1")

        # 第一次注册
        rec1 = store.register_comment(_comment(cid="c1"), source="sample")
        evidence_id1 = rec1.evidence_id

        # 第二次注册相同评论，不同源
        rec2 = store.register_comment(_comment(cid="c1"), source="drill")
        evidence_id2 = rec2.evidence_id

        # 验证幂等性：相同 ID 返回相同 evidence_id
        assert evidence_id1 == evidence_id2
        # 验证不创建新记录
        assert len(store.all_records()) == 1
        # 验证源列表包含两个源
        assert rec2.extra.get("sources") == ["sample", "drill"]

    def test_register_video_idempotent(self):
        """测试注册相同视频 ID 返回相同 evidence_id（幂等）。"""
        store = EvidenceStore(task_id="t1")

        # 第一次注册
        rec1 = store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="top")
        evidence_id1 = rec1.evidence_id

        # 第二次注册相同视频，不同源
        rec2 = store.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="drill")
        evidence_id2 = rec2.evidence_id

        # 验证幂等性：相同 ID 返回相同 evidence_id
        assert evidence_id1 == evidence_id2
        # 验证不创建新记录
        assert len(store.all_records()) == 1
        # 验证源列表包含两个源
        assert rec2.extra.get("sources") == ["top", "drill"]


class TestEvidenceReferenceGraph:
    def test_register_judgment_with_evidence_refs(self):
        """测试 judgment 登记并建立双向引用关系。"""
        store = EvidenceStore(task_id="t1")
        
        # 登记评论证据
        c1 = store.register_comment(_comment(cid="c1"), source="sample")
        c2 = store.register_comment(_comment(cid="c2"), source="sample")
        
        # 登记 judgment 引用 c1, c2
        j1 = store.register_judgment(
            judgment_type="risk",
            title="油耗争议升级",
            evidence_refs=[c1.evidence_id, c2.evidence_id],
            source="synthesize",
        )
        
        assert j1.kind == "judgment"
        assert j1.extra["judgment_type"] == "risk"
        assert j1.extra["title"] == "油耗争议升级"
        assert j1.extra["evidence_refs"] == [c1.evidence_id, c2.evidence_id]
        
        # 验证反向引用
        assert c1.referenced_by == [j1.evidence_id]
        assert c2.referenced_by == [j1.evidence_id]

    def test_register_assumption(self):
        """测试 assumption 登记。"""
        store = EvidenceStore(task_id="t1")
        a1 = store.register_assumption(
            title="竞品可能跟进",
            rationale="基于市场惯例推测",
            source="synthesize",
        )
        
        assert a1.kind == "assumption"
        assert a1.extra["title"] == "竞品可能跟进"
        assert a1.extra["rationale"] == "基于市场惯例推测"
        assert a1.source == "synthesize"

    def test_judgment_references_video_and_stat(self):
        """测试 judgment 引用视频与统计证据。"""
        store = EvidenceStore(task_id="t1")
        
        v1 = store.register_video(job_id="j1", video_title="坦克300", comment_count=100, source="top")
        s1 = store.register_stat(label="total", detail="总评论数", count=1000, source="coverage")
        
        j1 = store.register_judgment(
            judgment_type="opportunity",
            title="高热度视频",
            evidence_refs=[v1.evidence_id, s1.evidence_id],
            source="synthesize",
        )
        
        assert v1.referenced_by == [j1.evidence_id]
        assert s1.referenced_by == [j1.evidence_id]
