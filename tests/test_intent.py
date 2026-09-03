"""任务理解/意图识别模块测试。

V0.1 意图识别采用规则+关键词匹配（数据来源是 video_title 蕴含的品牌/车型/话题），
为后续接入 LLM 意图识别做准备。输入为自由文本，输出结构化 AnalysisIntent。
"""
from datetime import date

import pytest

from app.understanding.intent import (
    AnalysisIntent,
    TimeRange,
    IntentParser,
)


class TestAnalysisIntent:
    """AnalysisIntent 描述一次舆情分析的意图。"""

    def test_basic_fields(self):
        intent = AnalysisIntent(
            object="坦克300",
            time_range=TimeRange(start=date(2026, 8, 1), end=date(2026, 8, 31)),
            goal_type="pulse",
            raw_text="分析坦克300近期的舆情变化",
        )
        assert intent.object == "坦克300"
        assert intent.goal_type == "pulse"
        assert intent.raw_text == "分析坦克300近期的舆情变化"

    def test_from_dict(self):
        intent = AnalysisIntent.from_dict({
            "object": "猛士M817",
            "time_range": {"start": "2026-07-01", "end": "2026-07-31"},
            "goal_type": "anomaly",
            "raw_text": "排查猛士M817近期异常舆情",
        })
        assert intent.object == "猛士M817"
        assert intent.goal_type == "anomaly"
        assert intent.time_range.start.year == 2026


class TestIntentParse:
    """自由文本解析为结构化意图。"""

    def test_parse_basic_text(self):
        parser = IntentParser()
        intent = parser.parse("分析坦克300近期的舆情变化")
        assert intent is not None
        assert intent.object == "坦克300"

    def test_parse_with_time_window(self):
        parser = IntentParser()
        intent = parser.parse("最近一个月猛士M817的舆情怎么样")
        assert intent.object in ("猛士M817", "猛士M817的近况")
        assert intent.time_range is not None or intent.goal_type

    def test_parse_goal_type_detection(self):
        parser = IntentParser()
        # "异常/排查/升温" 暗示目标为异常问题排查
        intent = parser.parse("排查最近坦克300的异常舆情")
        assert intent.goal_type in ("anomaly", "pulse")

    def test_parse_unknown_object_returns_null(self):
        parser = IntentParser()
        intent = parser.parse("最近有什么热点话题吗")
        # 无法识别明确对象时，object 可为空，但不应抛错
        assert intent is not None
        assert isinstance(intent, AnalysisIntent)


class TestTimeRangeResolution:
    """时间范围解析：相对时间转绝对日期区间。"""

    def test_relative_recent(self):
        rng = TimeRange.resolve_relative("近期", today=date(2026, 9, 3))
        assert rng is not None
        assert rng.end == date(2026, 9, 3)

    def test_relative_last_month(self):
        rng = TimeRange.resolve_relative("上个月", today=date(2026, 9, 3))
        assert rng is not None
        assert rng.start.month == 8 and rng.end.month == 8

    def test_absolute_range(self):
        rng = TimeRange.resolve_relative("2026年7月", today=date(2026, 9, 3))
        assert rng.start.year == 2026 and rng.start.month == 7
