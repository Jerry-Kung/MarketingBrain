"""固定流水线 baseline 测试（工具用 stub，不连库）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import pytest

from app.pipeline.baseline import AnalysisBundle, BaselinePipeline
from app.store.evidence import EvidenceStore
from app.snapshot.snapshot import LogicalSnapshot


class StubToolset:
    """返回稳定的工具结果。"""
    def __init__(self, store):
        self.store = store
    def data_coverage(self, ds, snap, store):
        return {"name": "data_coverage", "result": {"comment_count": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def volume_trend(self, ds, snap, store):
        return {"name": "volume_trend", "result": {"total": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def period_comparison(self, ds, snap, store):
        return {"name": "period_comparison", "result": {"change_rate": 12.5},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}
    def topic_frequency_tool(self, ds, snap, store):
        return {"name": "topic_frequency", "result": {"topics": [{"topic": "油耗"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def top_sources(self, ds, snap, store):
        return {"name": "top_sources", "result": {"videos": [{"job_id": "j1"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def sample_comments(self, ds, snap, store):
        return {"name": "sample_comments", "result": {"comments": [{"comment_id": "c1"}]},
                "evidence_ids": [], "sample_size": 1, "time_range": {}, "bias_note": ""}
    def drill_evidence(self, ds, snap, store):
        return {"name": "drill_evidence", "result": {"comments": []},
                "evidence_ids": [], "sample_size": 0, "time_range": {}, "bias_note": ""}
    def object_compare(self, ds, snap, store):
        return {"name": "object_compare", "result": {"object_count": 100},
                "evidence_ids": [], "sample_size": 100, "time_range": {}, "bias_note": ""}


class RecordingStubToolset(StubToolset):
    """在 StubToolset 基础上记录调用顺序，用于验证固定阶段顺序。"""
    def __init__(self, store):
        super().__init__(store)
        self.calls = []

    def data_coverage(self, ds, snap, store):
        self.calls.append("data_coverage")
        return super().data_coverage(ds, snap, store)
    def volume_trend(self, ds, snap, store):
        self.calls.append("volume_trend")
        return super().volume_trend(ds, snap, store)
    def period_comparison(self, ds, snap, store):
        self.calls.append("period_comparison")
        return super().period_comparison(ds, snap, store)
    def topic_frequency_tool(self, ds, snap, store):
        self.calls.append("topic_frequency_tool")
        return super().topic_frequency_tool(ds, snap, store)
    def top_sources(self, ds, snap, store):
        self.calls.append("top_sources")
        return super().top_sources(ds, snap, store)
    def sample_comments(self, ds, snap, store):
        self.calls.append("sample_comments")
        return super().sample_comments(ds, snap, store)
    def drill_evidence(self, ds, snap, store):
        self.calls.append("drill_evidence")
        return super().drill_evidence(ds, snap, store)
    def object_compare(self, ds, snap, store):
        self.calls.append("object_compare")
        return super().object_compare(ds, snap, store)


class TestBaselinePipeline:
    def test_run_builds_bundle_with_all_stages(self):
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300", "goal_type": "pulse"},
        )
        store = EvidenceStore("t1")
        pipe = BaselinePipeline(
            datasource=None, snapshot=snap, evidence_store=store,
            toolset=StubToolset(store),
        )
        bundle = pipe.run()
        assert isinstance(bundle, AnalysisBundle)
        assert bundle.scope["comment_count"] == 100
        assert bundle.overall["change_rate"] == 12.5
        assert bundle.themes["topics"][0]["topic"] == "油耗"
        assert bundle.sources["videos"][0]["job_id"] == "j1"
        assert len(bundle.samples) == 1
        assert len(bundle.stats) >= 5

    def test_run_returns_serializable_dict(self):
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"]},
        )
        store = EvidenceStore("t1")
        pipe = BaselinePipeline(datasource=None, snapshot=snap, evidence_store=store,
                                toolset=StubToolset(store))
        d = pipe.run().to_dict()
        import json
        json.dumps(d)  # 必须可序列化

    def test_run_invokes_tools_in_fixed_order(self):
        """流水线必须按硬编码的固定顺序依次调用 8 个工具（无自主规划）。"""
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"]},
        )
        store = EvidenceStore("t1")
        toolset = RecordingStubToolset(store)
        pipe = BaselinePipeline(datasource=None, snapshot=snap, evidence_store=store,
                                toolset=toolset)
        pipe.run()
        assert toolset.calls == [
            "data_coverage", "volume_trend", "period_comparison",
            "topic_frequency_tool", "top_sources", "sample_comments",
            "drill_evidence", "object_compare",
        ]
