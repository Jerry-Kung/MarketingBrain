"""逻辑快照。

V0 采用“逻辑快照”而非复制几十万条原始数据：创建任务时记录数据源、过滤条件、
截止时间或最大数据ID。本次运行的所有工具调用自动附加相同快照边界，保证一次运行
内部口径一致。数据快照可序列化存入 SQLite，避免为 V0 建设数仓或对象存储。
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# 数据源标识（V0 固定为 api_job；后续可扩展）
DEFAULT_DATASOURCE = "api_job"

# 返回时区感知的 UTC 时间（替代已弃用的 datetime.utcnow）
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LogicalSnapshot:
    """一次分析任务的数据边界描述。

    - datasource: 数据源标识（如 api_job）
    - start_time/end_time: 数据时间窗口（UTC），可为 None 表示不限
    - max_comment_id: 最大评论ID边界（可选，用于增量一致性）
    - created_at: 快照创建时间
    - extra: 附加的过滤条件/边界（可扩展，如 video_title 过滤）
    """

    datasource: str = DEFAULT_DATASOURCE
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_comment_id: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转为可 JSON 序列化字典（datetime 转 iso 字符串）。"""
        return {
            "datasource": self.datasource,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "max_comment_id": self.max_comment_id,
            "created_at": self.created_at.isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogicalSnapshot":
        def _parse_dt(v):
            if not v:
                return None
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(v)

        return cls(
            datasource=d.get("datasource", DEFAULT_DATASOURCE),
            start_time=_parse_dt(d.get("start_time")),
            end_time=_parse_dt(d.get("end_time")),
            max_comment_id=d.get("max_comment_id"),
            created_at=_parse_dt(d.get("created_at")) or _utcnow(),
            extra=d.get("extra", {}),
        )

    @classmethod
    def from_intent(cls, intent) -> "LogicalSnapshot":
        """根据意图（含时间范围）构建快照。

        采纳意图的时间范围作为数据窗口；分析对象（品牌/车型/话题）暂存入 extra，
        供后续按对象过滤时使用（V0.1 先记录，不做硬性过滤）。
        """
        snap = cls(datasource=DEFAULT_DATASOURCE)
        if getattr(intent, "time_range", None):
            tr = intent.time_range
            snap.start_time = datetime(tr.start.year, tr.start.month, tr.start.day)
            snap.end_time = datetime(
                tr.end.year, tr.end.month, tr.end.day, 23, 59, 59
            )
        if getattr(intent, "object", None):
            snap.extra["object"] = intent.object
        if getattr(intent, "goal_type", None):
            snap.extra["goal_type"] = intent.goal_type
        return snap

    def attach_query_params(self, params: dict) -> dict:
        """向查询参数附加快照边界（时间窗、最大ID），保证一次运行口径一致。"""
        p = dict(params)
        if self.start_time:
            p.setdefault("start_time", self.start_time)
        if self.end_time:
            p.setdefault("end_time", self.end_time)
        if self.max_comment_id:
            p.setdefault("max_comment_id", self.max_comment_id)
        return p
