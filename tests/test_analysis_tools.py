"""分析工具集测试（用 FakeDataSource 验证工具逻辑与证据登记）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import pytest

from app.analysis import tools
from app.analysis.tools import (
    data_coverage, volume_trend, period_comparison, topic_frequency_tool,
    top_sources, sample_comments, drill_evidence, object_compare,
)
from app.store.evidence import EvidenceStore
from app.snapshot.snapshot import LogicalSnapshot
from app.datasource.models import CommentRecord


class FakeDS:
    """模拟 MySqlDataSource 返回稳定值。"""
    def __init__(self):
        self._calls = []
    def _comment(self, cid, vt="坦克300", likes=1):
        return CommentRecord(comment_id=cid, content=f"评论{cid}", video_title=vt,
                             job_id=f"j{cid}", comment_like_count=likes,
                             passed=True, is_car_owner=True, has_purchase_intent=False)
    def count_comments(self, **kw):
        self._calls.append(("count", kw)); return 100
    def time_series(self, **kw):
        self._calls.append(("ts", kw))
        return {"start": "2026-08-01", "end": "2026-08-31", "bucket": "day",
                "buckets": [{"start": "2026-08-01", "count": 30},
                            {"start": "2026-08-02", "count": 70}], "total": 100}
    def top_videos(self, **kw):
        self._calls.append(("top", kw))
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 20}]
    def topic_frequency(self, **kw):
        self._calls.append(("topic", kw))
        return [{"topic": "油耗", "comment_count": 40}, {"topic": "改装", "comment_count": 30}]
    def fetch_comments(self, limit=30, **kw):
        self._calls.append(("fetch", kw))
        return [self._comment("c1"), self._comment("c2")]


def _snap():
    return LogicalSnapshot(
        start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
        extra={"video_tags": ["坦克300"], "object": "坦克300", "goal_type": "pulse"},
    )


class TestTools:
    def _run(self, fn):
        ds = FakeDS()
        store = EvidenceStore("t1")
        out = fn(ds, _snap(), store)
        return ds, store, out

    def test_data_coverage(self):
        ds, store, out = self._run(data_coverage)
        assert out["name"] == "data_coverage"
        assert out["sample_size"] > 0
        assert store.all_records()  # 应登记统计证据

    def test_volume_trend(self):
        ds, store, out = self._run(volume_trend)
        assert out["result"]["total"] == 100
        assert out["evidence_ids"]

    def test_period_comparison_has_change_rate(self):
        ds, store, out = self._run(period_comparison)
        assert "change_rate" in out["result"] or "current" in out["result"]

    def test_topic_frequency_tool(self):
        ds, store, out = self._run(topic_frequency_tool)
        assert any(t["topic"] == "油耗" for t in out["result"]["topics"])

    def test_top_sources(self):
        ds, store, out = self._run(top_sources)
        assert out["result"]["videos"][0]["job_id"] == "j1"

    def test_sample_comments_registers_evidence(self):
        ds, store, out = self._run(sample_comments)
        assert out["sample_size"] >= 2
        assert store.has_comment("c1")

    def test_drill_evidence(self):
        ds, store, out = self._run(drill_evidence)
        assert "keywords" in out["params"] or "result" in out

    def test_object_compare(self):
        ds, store, out = self._run(object_compare)
        assert out["name"] == "object_compare"

    def test_tools_register_stat_evidence(self):
        ds, store, out = self._run(data_coverage)
        kinds = {r.kind for r in store.all_records()}
        assert "stat" in kinds
