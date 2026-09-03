"""任务理解/意图识别模块。

V0.1 采用规则 + 关键词匹配实现对自由文本的意图解析，为后续接入 LLM
意图识别（V0.2+）打基础。目标是把用户的自然语言输入，解析为结构化的
AnalysisIntent（分析对象、时间周期、目标类型）。

这不是老式“选择车型”的 IT 模式——输入始终是自由文本，解析过程由本模块
完成，未来升级为 LLM 意图识别时，本模块接口保持不变。
"""
import re
from collections import namedtuple
from datetime import date, timedelta
from typing import Optional


class TimeRange:
    """数据时间范围（UTC 日期区间）。"""

    def __init__(self, start: date, end: date):
        self.start = start
        self.end = end

    def __repr__(self):
        return f"TimeRange({self.start} ~ {self.end})"

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_dict(cls, d: dict) -> "TimeRange":
        return cls(
            start=date.fromisoformat(d["start"]),
            end=date.fromisoformat(d["end"]),
        )

    @classmethod
    def resolve_relative(
        cls, phrase: str, today: Optional[date] = None
    ) -> Optional["TimeRange"]:
        """将相对时间短语解析为绝对日期区间。

        - "近期"/"最近"/"近段时间"  -> 最近 30 天
        - "本周" -> 本周（周一起）
        - "上周"/"上个月"/"上月" -> 上一周期
        - "今天"/"昨日" -> 单日
        - 绝对格式  YYYY年M月  -> 整月
        """
        today = today or date.today()
        phrase = phrase.strip()

        # 绝对月份：YYYY年M月 或 YYYY-MM
        m = re.search(r"(\d{4})[年\-/](\d{1,2})月?", phrase)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            start = date(y, mo, 1)
            if mo == 12:
                end = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(y, mo + 1, 1) - timedelta(days=1)
            return cls(start, end)

        # 近期 / 最近 / 近段时间
        if any(k in phrase for k in ["近期", "最近", "近段", "这段时间"]):
            return cls(today - timedelta(days=30), today)

        # 本周（周一为一周起点）
        if "本周" in phrase:
            start = today - timedelta(days=today.weekday())
            return cls(start, today)

        # 上周
        if "上周" in phrase or "上个星期" in phrase:
            start = today - timedelta(days=today.weekday() + 7)
            return cls(start, start + timedelta(days=6))

        # 上个月
        if "上个月" in phrase or "上月" in phrase:
            first_this = today.replace(day=1)
            prev_end = first_this - timedelta(days=1)
            return cls(prev_end.replace(day=1), prev_end)

        # 今天
        if "今天" in phrase or "今日" in phrase:
            return cls(today, today)

        # 昨日
        if "昨日" in phrase or "昨天" in phrase:
            y = today - timedelta(days=1)
            return cls(y, y)

        # 最近 N 天 / 近 N 天
        m = re.search(r"(?:最近|近)\s*(\d+)\s*天", phrase)
        if m:
            n = int(m.group(1))
            return cls(today - timedelta(days=n - 1), today)

        return None


class AnalysisIntent:
    """一次舆情分析的意图描述。"""

    def __init__(
        self,
        object: str,                     # 分析对象：品牌/车型/话题
        time_range: Optional[TimeRange],
        goal_type: str,                  # pulse(脉搏) / anomaly(异常) / drill(专题)
        raw_text: str = "",
    ):
        self.object = object
        self.time_range = time_range
        self.goal_type = goal_type
        self.raw_text = raw_text

    def __repr__(self):
        return (
            f"AnalysisIntent(object={self.object!r}, "
            f"goal_type={self.goal_type!r}, "
            f"time_range={self.time_range!r})"
        )

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisIntent":
        tr = d.get("time_range")
        return cls(
            object=d.get("object", ""),
            time_range=TimeRange.from_dict(tr) if tr else None,
            goal_type=d.get("goal_type", "pulse"),
            raw_text=d.get("raw_text", ""),
        )

    def to_dict(self) -> dict:
        return {
            "object": self.object,
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "goal_type": self.goal_type,
            "raw_text": self.raw_text,
        }


# 内置知识：常见汽车品牌/车型/话题关键词
# 后续可升级为数据驱动的知识库（从 video_title 挖掘），此处作为规则基线
BUILTIN_KEYWORDS = [
    # 品牌
    "坦克", "猛士", "魏牌", "领克", "星越", "极氪", "理想", "蔚来", "小鹏",
    "比亚迪", "方程豹", "奇瑞", "吉利", "长安", "长城", "哈弗", "问界",
    "尊界", "智界", "享界", "阿维塔", "深蓝", "风云", "极石", "五菱", "埃安",
    "奔驰", "宝马", "奥迪", "大众", "丰田", "本田", "特斯拉",
    # 车型（跟品牌区分度更高的具体名）
    "M817", "坦克300", "坦克500", "领克01", "星越L", "雅阁", "奔驰C级", "沃尔沃",
    "钛9", "风云A9", "牧马人", "猛士M817", "泰钽700", "方程豹钛9", "尊界S800",
    "ADS5", "ADS 5", "华为乾崑", "卡线造车",
]


class IntentParser:
    """规则 + 关键词的意图解析器。

    用法：
        parser = IntentParser()
        intent = parser.parse("分析猛士M817近期的舆情变化")
        # -> AnalysisIntent(object="猛士M817", goal_type="pulse", time_range=近30天)
    """

    def __init__(self, keywords: Optional[list[str]] = None):
        self.keywords = keywords or BUILTIN_KEYWORDS

    def parse(self, text: str, today: Optional[date] = None) -> AnalysisIntent:
        text = (text or "").strip()
        today = today or date.today()

        obj = self._extract_object(text)
        time_range = TimeRange.resolve_relative(text, today=today)
        goal_type = self._detect_goal(text)

        return AnalysisIntent(
            object=obj,
            time_range=time_range,
            goal_type=goal_type,
            raw_text=text,
        )

    def _extract_object(self, text: str) -> str:
        """从文本中提取分析对象（优先匹配更长、更具体的词）。

        按关键词长度降序匹配，优先命中“猛士M817”这类复合词而非“猛士”。
        """
        hits = [kw for kw in self.keywords if kw and kw in text]
        if not hits:
            return ""
        # 取命中最长、出现位置最靠前的关键词
        hits.sort(key=lambda k: (-len(k), text.index(k)))
        return hits[0]

    def _detect_goal(self, text: str) -> str:
        """识别目标类型：pulse / anomaly / drill。"""
        if any(k in text for k in ["排查", "异常", "升温", "突发", "危机", "爆雷"]):
            return "anomaly"
        if any(k in text for k in ["专题", "下钻", "深入", "聚焦", "对比"]):
            return "drill"
        # 默认常规脉搏
        return "pulse"
