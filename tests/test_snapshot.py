"""逻辑快照测试。

V0 采用“逻辑快照”而非复制原始数据：记录数据源、过滤条件、截止时间或最大数据ID，
本次运行的所有工具调用自动附加相同边界，保证口径一致。
"""
from datetime import datetime

import pytest

from app.snapshot.snapshot import LogicalSnapshot


class TestLogicalSnapshot:
    """逻辑快照记录一次运行的数据边界。"""

    def test_create_with_time_window(self):
        snap = LogicalSnapshot(
            datasource="api_job",
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
        )
        assert snap.datasource == "api_job"
        assert snap.start_time == datetime(2026, 8, 1)
        assert snap.end_time == datetime(2026, 8, 31)

    def test_snapshot_has_created_at(self):
        snap = LogicalSnapshot(
            datasource="api_job",
            start_time=None,
            end_time=None,
        )
        assert snap.created_at is not None

    def test_to_dict_roundtrip(self):
        snap = LogicalSnapshot(
            datasource="api_job",
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
        )
        d = snap.to_dict()
        assert d["datasource"] == "api_job"
        snap2 = LogicalSnapshot.from_dict(d)
        assert snap2.start_time == snap.start_time
        assert snap2.end_time == snap.end_time

    def test_snapshot_boundary_serializable(self):
        """快照边界应能序列化（存 SQLite）。"""
        import json
        snap = LogicalSnapshot(
            datasource="api_job",
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
        )
        # start_time/end_time 存为 iso 字符串，created_at 同理
        payload = json.dumps(snap.to_dict())
        assert "2026-08-01" in payload

    def test_build_from_intent(self):
        """可由意图（含时间范围）构建快照。"""
        from app.understanding.intent import AnalysisIntent, IntentParser, TimeRange
        intent = IntentParser().parse("分析坦克300近期的舆情", today=datetime(2026, 9, 3).date())
        snap = LogicalSnapshot.from_intent(intent)
        assert snap.datasource == "api_job"
        # 快照应采纳意图的时间范围
        if intent.time_range:
            assert snap.start_time is not None
            assert snap.end_time is not None
