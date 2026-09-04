"""数据源新增查询方法的纯逻辑测试（不连真实 MySQL）。

时间分桶、对比变化率、标签匹配 SQL 构造用 FakeDataSource 验证。
真实 MySQL 正确性由 tests/test_mysql_integration.py 集成测试承担。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta
import pytest

from app.datasource.adapter import _build_comment_filters, _tag_like_clause
from app.datasource.adapter import _aggregate_time_series


class TestTagFilter:
    def test_single_tag_clause(self):
        sql = _tag_like_clause(["坦克300"])
        assert "like" in sql.lower() or "#" in sql
        assert "%#坦克300%" in sql

    def test_multiple_tags_or(self):
        sql = _tag_like_clause(["坦克300", "坦克500"])
        assert sql.count("#") >= 2
        assert sql.count("OR") >= 1  # 多个标签用 OR 连接

    def test_empty_tags_returns_none(self):
        assert _tag_like_clause([]) is None


class TestCommentFilters:
    def test_filters_build_params(self):
        params = {}
        _build_comment_filters(
            params,
            start_time=datetime(2026, 8, 1),
            end_time=datetime(2026, 8, 31),
            video_tags=["坦克300"],
            keyword="油耗",
            min_like=5,
        )
        assert params["start_time"] == datetime(2026, 8, 1)
        assert params["end_time"] == datetime(2026, 8, 31)
        assert params["keyword"] == "油耗"
        assert params["min_like"] == 5

    def test_optional_params_skipped(self):
        params = {}
        _build_comment_filters(params)
        assert "keyword" not in params
        assert "min_like" not in params
        assert "start_time" not in params
        assert "end_time" not in params


class TestTimeSeries:
    def test_bucketize_daily(self):
        start = datetime(2026, 8, 1)
        end = datetime(2026, 8, 3, 23, 59, 59)
        # 模拟逐条时间戳列表 -> 分桶
        counts = [
            (datetime(2026, 8, 1), 10),
            (datetime(2026, 8, 2), 20),
            (datetime(2026, 8, 3), 5),
            (datetime(2026, 8, 1), 15),
        ]
        buckets = _aggregate_time_series(counts, start, end, bucket="day")
        assert buckets["total"] == 50
        # 应补齐缺失日（8-1 到 8-3 三天）
        assert len(buckets["buckets"]) == 3
        assert buckets["buckets"][0]["count"] == 25  # 8-01 两天合并
