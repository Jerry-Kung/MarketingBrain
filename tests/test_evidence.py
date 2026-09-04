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
        store = EvidenceStore(task_id="t1")
        store.register_comment(_comment(), source="sample")
        recs = store.all_records()
        assert recs[0].is_car_owner is True
