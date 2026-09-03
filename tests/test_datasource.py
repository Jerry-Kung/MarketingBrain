"""数据源查询与数据模型测试。

使用纯逻辑测试（不连真实数据库）验证 JSON 展开逻辑正确性。
数据源查询的集成测试在独立测试中通过真实 MySQL 验证。
"""
import json
from datetime import datetime

import pytest

from app.datasource.models import CommentRecord


class TestCommentRecord:
    """CommentRecord 数据类承载一条展开后的评论。"""

    def test_creates_from_dict(self):
        record = CommentRecord(
            comment_id="7658959462686491419",
            content="带华为智驾的10几万的多了去了",
            video_title="尊界S800 ADS 5首发",
            comment_author="Ggggsdddfr",
            comment_like_count=3,
            passed=True,
            is_car_owner=False,
            has_purchase_intent=False,
            analysis="吐槽定价过高",
            job_id="58216c8b-4cc8-4602-a056-dafaf8d7e27c",
            job_created_at=datetime(2026, 7, 24, 12, 45, 54),
        )
        assert record.comment_id == "7658959462686491419"
        assert record.video_title == "尊界S800 ADS 5首发"

    def test_default_passed_when_none(self):
        import dataclasses
        # passed 为 None 时默认 False（未通过初筛/无 result）
        record = CommentRecord(
            comment_id="1", content="c", video_title="v",
            comment_author="a", comment_like_count=0,
        )
        assert record.passed is False


class TestPayloadParsing:
    """验证从 api_job.request_payload 展开 comments 数组的解析逻辑。"""

    def test_parse_comments_from_payload(self):
        from app.datasource.queries import parse_comments_from_payload

        payload = json.dumps({
            "comments": [
                {
                    "comment_id": "7658959462686491419",
                    "video_title": "尊界S800",
                    "video_author": "@老王说车",
                    "comment_content": "这车智驾确实牛",
                    "comment_author": "用户_7823",
                    "comment_author_uid": "MS4wLjABAAAA",
                    "comment_time": "2026-07-19T14:23:00+08:00",
                    "comment_like_count": 234,
                }
            ]
        }, ensure_ascii=False)
        comments = parse_comments_from_payload(payload)
        assert len(comments) == 1
        assert comments[0]["comment_id"] == "7658959462686491419"
        assert comments[0]["comment_content"] == "这车智驾确实牛"
        assert comments[0]["comment_like_count"] == 234

    def test_parse_empty_payload(self):
        from app.datasource.queries import parse_comments_from_payload

        assert parse_comments_from_payload("{}") == []
        assert parse_comments_from_payload(json.dumps({"comments": []})) == []

    def test_parse_missing_content_safe(self):
        from app.datasource.queries import parse_comments_from_payload

        payload = json.dumps({"comments": [{"comment_id": "1"}]})
        comments = parse_comments_from_payload(payload)
        assert len(comments) == 1
        assert comments[0].get("comment_content", "") == ""
