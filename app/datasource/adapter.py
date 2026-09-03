"""MySQL 只读数据源适配器。

关键约束（V0 架构原则）：
- 只读：只通过预定义方法查询，绝不暴露任意 SQL 执行接口。
- 走 SQLAlchemy engine，但所有查询由本适配器控制，不允许 Agent/调用方传原始 SQL。
- 所有结果携带查询条件、样本量、时间范围，供证据链登记与审计。

数据源为测试环境 MySQL 的驱动表 api_job（正式数据约 97 万条评论）。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.datasource.models import CommentRecord
from app.datasource.queries import SQL_COUNT, SQL_EXPAND_COMMENTS

# 强制只读：连接后设置会话，确保任何误用都不会写库。
# （账号本身只授 SELECT，双保险。）
_READ_ONLY_SESSION = "SET SESSION TRANSACTION READ ONLY"


@dataclass
class DataOverview:
    """数据覆盖概览：整体规模与时间范围。"""

    job_count: int
    comment_count: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]


class MySqlDataSource:
    """测试环境 MySQL 只读数据源适配器。

    仅暴露能力有限的预定义方法；调用方无法执行任意 SQL。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- 内部工具 ----
    def _execute(self, sql: str, params: dict | None = None):
        """执行只读查询。每个连接强制只读事务。"""
        params = params or {}
        with self._engine.connect() as conn:
            conn.exec_driver_sql(_READ_ONLY_SESSION)
            result = conn.execute(text(sql), params)
            return result

    def _row_to_comment(self, row) -> CommentRecord:
        """将查询行转换为 CommentRecord。

        处理 MySQL 返回的布尔/None 与字段别名映射。
        """
        # MySQL JSON_TABLE 返回 1/0/None (tinyint)，统一转 bool
        passed = row.passed if row.passed is not None else False
        is_car_owner = None if row.is_car_owner is None else bool(row.is_car_owner)
        has_intent = None if row.has_purchase_intent is None else bool(row.has_purchase_intent)

        like_count = row.comment_like_count if row.comment_like_count is not None else 0

        return CommentRecord(
            comment_id=str(row.comment_id),
            content=row.comment_content or "",
            video_title=row.video_title or "",
            comment_author=row.comment_author or "",
            comment_author_uid=row.comment_author_uid or "",
            comment_like_count=int(like_count),
            passed=bool(passed),
            is_car_owner=is_car_owner,
            has_purchase_intent=has_intent,
            analysis=row.analysis,
            job_id=str(row.job_id),
            job_status=row.job_status,
            job_created_at=row.job_created_at,
        )

    # ---- 预定义查询方法 ----
    def fetch_comments(self, limit: int = 500) -> list[CommentRecord]:
        """拉取成功作业的评论列表（含初筛结果）。

        参数 limit 限制返回条数（防止单次拉取过大）。
        """
        # 子查询包一层，limit 作用于展开后的行
        sql = f"SELECT * FROM ({SQL_EXPAND_COMMENTS}) AS expanded LIMIT :lim"
        result = self._execute(sql, {"lim": int(limit)})
        return [self._row_to_comment(row) for row in result.mappings()]

    def data_overview(self) -> DataOverview:
        """返回数据覆盖概览：作业数、评论数、时间范围。"""
        with self._engine.connect() as conn:
            conn.exec_driver_sql(_READ_ONLY_SESSION)
            # 作业数与评论数
            cnt = conn.execute(text(SQL_COUNT)).one()
            job_count = int(cnt.job_count or 0)
            comment_count = int(cnt.comment_count or 0)

            # 时间范围
            trange = conn.execute(
                text("SELECT MIN(created_at), MAX(created_at) "
                     "FROM api_job WHERE job_type='comment_screening'")
            ).one()
            start_time = trange[0] if trange and trange[0] else None
            end_time = trange[1] if trange and trange[1] else None

        return DataOverview(
            job_count=job_count,
            comment_count=comment_count,
            start_time=start_time,
            end_time=end_time,
        )

    def distinct_video_titles(self, limit: int = 100) -> list[dict]:
        """返回评论数 Top 的视频标题列表（用于任务理解/意图识别的候选主题）。"""
        sql = """
        SELECT c.vt AS video_title, COUNT(*) AS comment_count
        FROM api_job j
        CROSS JOIN JSON_TABLE(
            j.request_payload, '$.comments[*]'
            COLUMNS (vt TEXT PATH '$.video_title')
        ) c
        WHERE j.job_type = 'comment_screening' AND j.status = 'success'
        GROUP BY c.vt
        ORDER BY comment_count DESC
        LIMIT :lim
        """
        result = self._execute(sql, {"lim": int(limit)})
        return [
            {"video_title": row.video_title, "comment_count": int(row.comment_count)}
            for row in result.mappings()
        ]
