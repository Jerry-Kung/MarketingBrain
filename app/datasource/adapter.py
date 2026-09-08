"""MySQL 只读数据源适配器。

关键约束（V0 架构原则）：
- 只读：只通过预定义方法查询，绝不暴露任意 SQL 执行接口。
- 走 SQLAlchemy engine，但所有查询由本适配器控制，不允许 Agent/调用方传原始 SQL。
- 所有结果携带查询条件、样本量、时间范围，供证据链登记与审计。

数据源为测试环境 MySQL 的驱动表 api_job（正式数据约 97 万条评论）。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.datasource.models import CommentRecord
from app.datasource.queries import SQL_COUNT, build_comment_query

# 强制只读：连接后设置会话，确保任何误用都不会写库。
# （账号本身只授 SELECT，双保险。）
_READ_ONLY_SESSION = "SET SESSION TRANSACTION READ ONLY"


def _tag_like_clause(video_tags) -> Optional[str]:
    """根据话题标签列表生成 video_title 匹配的子句（按 #标签 精确匹配，防竞品误算）。

    返回形如 "(c.vt LIKE '%#坦克300%' OR c.vt LIKE '%#坦克500%')" 或 None。
    标签间以空格/换行分隔，标签值前拼接 # 但不要求尾部有 #；标签值中的单引号转义为两个单引号。
    """
    if not video_tags:
        return None
    clause = " OR ".join(
        f"c.vt LIKE '%#{str(t).replace(chr(39), chr(39) * 2)}%'" for t in video_tags if t
    )
    return f"({clause})" if clause else None


def _time_tag_conds(params: dict) -> list[str]:
    """根据 params 中已写入的键，构造时间窗口与标签匹配的 SQL 条件片段列表。

    时间条件仅在 params 含 start_time/end_time 键时才生成（可选窗口）；
    标签条件仅在 _tag_like_clause 返回非 None 时才加入（video_tags=[""] 之类
    的全空标签列表会被 _tag_like_clause 过滤掉，此处需同步跳过，避免把 None
    拼进 SQL 条件列表）。供 _comment_base/top_videos/topic_frequency 复用。
    """
    conds = []
    if "start_time" in params:
        conds.append("j.created_at >= :start_time")
    if "end_time" in params:
        conds.append("j.created_at <= :end_time")
    if params.get("video_tags"):
        tag_clause = _tag_like_clause(params["video_tags"])
        if tag_clause is not None:
            conds.append(tag_clause)
    return conds


def _build_comment_filters(params: dict, *, start_time: Optional[datetime] = None,
                            end_time: Optional[datetime] = None,
                            video_tags=None, keyword: Optional[str] = None,
                            min_like: Optional[int] = None,
                            passed: Optional[bool] = None,
                            has_purchase_intent: Optional[bool] = None,
                            is_car_owner: Optional[bool] = None) -> dict:
    """向 params 写入过滤条件对应的绑定参数（不写 SQL 文本，只写绑定量）。

    时间边界用 job.created_at（唯一时间锚点）；对象用 #标签；关键字用 comment_content LIKE。
    所有参数仅在非 None（video_tags 为非空）时写入，调用方据此判断是否拼接对应 SQL 条件。
    """
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if video_tags:
        params["video_tags"] = video_tags
    if keyword is not None:
        params["keyword"] = keyword
    if min_like is not None:
        params["min_like"] = min_like
    if passed is not None:
        params["passed"] = bool(passed)
    if has_purchase_intent is not None:
        params["has_purchase_intent"] = bool(has_purchase_intent)
    if is_car_owner is not None:
        params["is_car_owner"] = bool(is_car_owner)
    return params


def _aggregate_time_series(rows, start: datetime, end: datetime, bucket: str = "day") -> dict:
    """把 [(datetime, count), ...] 按桶聚合，补齐缺失区间。

    rows 是逐条计数（无需预先排序）。返回 {"start","end","bucket","buckets":[...],"total"}。
    缺桶自动补 count=0，保证时间轴完整；buckets 按时间升序排列。
    """
    from collections import OrderedDict

    start = start.replace(tzinfo=None)
    end = end.replace(tzinfo=None)
    buckets: "OrderedDict[str, int]" = OrderedDict()
    cur = start
    if bucket == "day":
        delta = timedelta(days=1)
    else:
        raise ValueError(f"不支持的 bucket: {bucket}")
    while cur <= end:
        buckets[cur.date().isoformat()] = 0
        cur += delta
    for ts, count in rows:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        elif not isinstance(ts, datetime):
            # datetime.date 等无 tzinfo 属性的情况，先转为 datetime
            ts = datetime(ts.year, ts.month, ts.day)
        ts = ts.replace(tzinfo=None)
        key = ts.date().isoformat()
        if key in buckets:
            buckets[key] += count
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket": bucket,
        "buckets": [{"start": k, "count": v} for k, v in buckets.items()],
        "total": sum(buckets.values()),
    }


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

    @classmethod
    def from_settings(cls, settings) -> "MySqlDataSource":
        """按配置构造数据源（供冒烟测试/生产入口复用）。

        与 app/api/routes.py 的 create_app_from_settings 保持一致：
        - engine 由 settings.db_url 构建，URL 编码密码。
        - pool_pre_ping/recycle 处理连接失效，size 控制连接池。
        - db_url 会触发 _validate_required_db 校验缺项。
        """
        from sqlalchemy import create_engine

        engine = create_engine(
            settings.db_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=3,
            max_overflow=2,
        )
        return cls(engine)

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

    # ---- 内部工具：带过滤条件的评论子查询 ----
    def _comment_base(self, params: dict) -> str:
        """构造带过滤条件的展开评论 SQL（不含 LIMIT/ORDER BY，由调用方在外层拼接）。

        时间条件仅在 params 中存在 start_time/end_time 时才拼接（可选窗口）。
        """
        sql = build_comment_query()
        conds = _time_tag_conds(params)
        if params.get("keyword"):
            conds.append("c.c LIKE CONCAT('%', :keyword, '%')")
        if params.get("min_like") is not None:
            conds.append("c.like_count >= :min_like")
        if params.get("passed") is not None:
            conds.append("r.passed = :passed")
        if params.get("has_purchase_intent") is not None:
            conds.append("r.has_purchase_intent = :has_purchase_intent")
        if params.get("is_car_owner") is not None:
            conds.append("r.is_car_owner = :is_car_owner")
        if conds:
            sql += " AND " + " AND ".join(conds)
        return sql

    # ---- 预定义查询方法 ----
    def fetch_comments(self, limit: int = 500, *, start_time: Optional[datetime] = None,
                        end_time: Optional[datetime] = None, video_tags=None,
                        keyword: Optional[str] = None, min_like: Optional[int] = None,
                        passed: Optional[bool] = None,
                        has_purchase_intent: Optional[bool] = None,
                        is_car_owner: Optional[bool] = None,
                        order_by: Optional[str] = None) -> list[CommentRecord]:
        """拉取符合过滤条件的评论列表（含初筛结果），限 limit 条。

        所有过滤参数均可选；不传时行为与 V0.1 等价（成功作业的全部评论）。
        order_by 支持 "random"（随机抽样）与 "likes"（按点赞数降序）。
        """
        params: dict = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time,
            video_tags=video_tags, keyword=keyword, min_like=min_like,
            passed=passed, has_purchase_intent=has_purchase_intent,
            is_car_owner=is_car_owner,
        )
        # ORDER BY 作用在外层（外层别名 comment_like_count），而不是内层派生表，
        # 避免 MySQL 对派生表内 ORDER BY 的优化器行为不确定（结果可能不保证顺序）。
        order = ""
        if order_by == "random":
            order = " ORDER BY RAND()"
        elif order_by == "likes":
            order = " ORDER BY comment_like_count DESC"
        sql = f"SELECT * FROM ({self._comment_base(params)}) AS expanded{order} LIMIT :lim"
        params["lim"] = int(limit)
        result = self._execute(sql, params)
        return [self._row_to_comment(row) for row in result.mappings()]

    def count_comments(self, *, start_time: Optional[datetime] = None,
                        end_time: Optional[datetime] = None, video_tags=None,
                        keyword: Optional[str] = None) -> int:
        """统计符合过滤条件的评论条数（口径与 fetch_comments 一致）。"""
        params: dict = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time,
            video_tags=video_tags, keyword=keyword,
        )
        sql = f"SELECT COUNT(*) AS c FROM ({self._comment_base(params)}) AS x"
        result = self._execute(sql, params)
        return int(result.one().c or 0)

    def time_series(self, *, start_time: datetime, end_time: datetime,
                     video_tags=None, bucket: str = "day") -> dict:
        """按天（或指定粒度）统计窗口内评论数，补齐缺失区间形成完整时间轴。"""
        params: dict = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        sql = (
            "SELECT DATE(j.created_at) AS d, COUNT(*) AS n FROM api_job j "
            "CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (cid VARCHAR(64) PATH '$.comment_id', "
            "vt TEXT PATH '$.video_title')) c "
            "WHERE j.job_type='comment_screening' AND j.status='success' "
            "AND j.created_at >= :start_time AND j.created_at <= :end_time"
        )
        if params.get("video_tags"):
            sql += " AND " + _tag_like_clause(params["video_tags"])
        sql += " GROUP BY d ORDER BY DATE(j.created_at)"
        result = self._execute(sql, params)
        rows = []
        for row in result.mappings():
            ts = row.d
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            else:
                # MySQL DATE() 返回 datetime.date；转为当天起点的 datetime
                ts = datetime(ts.year, ts.month, ts.day)
            rows.append((ts, int(row.n or 0)))
        return _aggregate_time_series(rows, start_time, end_time, bucket=bucket)

    def top_videos(self, *, start_time: Optional[datetime] = None,
                    end_time: Optional[datetime] = None, video_tags=None,
                    limit: int = 10) -> list[dict]:
        """按评论数排序返回窗口内的 Top 视频（按 video_title 聚合）。

        一个 api_job 只是一批 <=50 条评论的采集批次，同一视频常拆分为多个
        job（例如某标题在 8 月对应 32 个 job / 1510 条评论），因此按 job_id
        聚合毫无意义（全部封顶在 50）。这里改为按 c.vt（video_title）聚合，
        job_id 取该标题下最小的 job id 作为代表性来源标识，另附 job_count
        说明该标题实际由多少个 job 汇总而来。
        """
        params: dict = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        sql = (
            "SELECT MIN(j.id) AS job_id, c.vt AS video_title, "
            "COUNT(*) AS comment_count, "
            "COALESCE(SUM(c.like_count),0) AS like_sum, "
            "COUNT(DISTINCT j.id) AS job_count "
            "FROM api_job j CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (cid VARCHAR(64) PATH '$.comment_id', "
            "vt TEXT PATH '$.video_title', `like_count` INT PATH '$.comment_like_count')) c "
            "WHERE j.job_type='comment_screening' AND j.status='success'"
        )
        conds = _time_tag_conds(params)
        if conds:
            sql += " AND " + " AND ".join(conds)
        sql += " GROUP BY c.vt ORDER BY comment_count DESC LIMIT :lim"
        params["lim"] = int(limit)
        result = self._execute(sql, params)
        return [
            {"job_id": str(row.job_id), "video_title": row.video_title or "",
             "comment_count": int(row.comment_count or 0), "like_sum": int(row.like_sum or 0),
             "job_count": int(row.job_count or 0)}
            for row in result.mappings()
        ]

    def topic_frequency(self, *, start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None, video_tags=None,
                         limit: int = 50) -> list[dict]:
        """统计窗口内评论所属视频标题的高频话题标签（不含对象本身的标签）。

        用于给 LLM 提供候选子主题。对象标签（如 #坦克300）会出现在几乎所有
        标题里，为避免无意义，这里按出现次数排序，由调用方决定是否排除对象标签。
        """
        params: dict = {}
        _build_comment_filters(
            params, start_time=start_time, end_time=end_time, video_tags=video_tags,
        )
        # 用正则式在应用层解析 hashtag（MySQL 端难做中文分词）
        sql = (
            "SELECT c.vt AS video_title FROM api_job j "
            "CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]' "
            "COLUMNS (vt TEXT PATH '$.video_title')) c "
            "WHERE j.job_type='comment_screening' AND j.status='success'"
        )
        conds = _time_tag_conds(params)
        if conds:
            sql += " AND " + " AND ".join(conds)
        result = self._execute(sql, params)
        import re
        from collections import Counter
        counter: "Counter[str]" = Counter()
        for row in result.mappings():
            for tag in re.findall(r"#([^#\s]+)", row.video_title or ""):
                counter[tag] += 1
        return [{"topic": t, "comment_count": n} for t, n in counter.most_common(limit)]

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
