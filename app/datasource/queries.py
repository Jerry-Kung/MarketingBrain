"""数据源查询与解析。

包含两部分：
1. `parse_comments_from_payload` —— 纯逻辑：从 api_job.request_payload 的 JSON
   字符串解析出 comments 数组。用于在应用层解析，或在不需要 DB JSON_TABLE 时降级使用。
2. SQL 常量 —— 通过 JSON_TABLE 在 MySQL 端展开的预定义查询。
"""
import json

# JSON_TABLE 在 MySQL 端展开 request_payload.comments[*]。
# 注意：like 是 MySQL 保留字，列别名用反引号 \`like_count\`。
# 该 SQL 返回每条评论一行。__（双下划线）内部字段名与映射列对应。
SQL_EXPAND_COMMENTS = """
SELECT
    j.id                         AS job_id,
    j.status                     AS job_status,
    j.created_at                 AS job_created_at,
    c.cid                        AS comment_id,
    c.c                          AS comment_content,
    c.vt                         AS video_title,
    c.at                         AS comment_author,
    c.uid                        AS comment_author_uid,
    c.like_count                 AS comment_like_count,
    r.passed,
    r.is_car_owner,
    r.has_purchase_intent,
    r.analysis
FROM api_job j
CROSS JOIN JSON_TABLE(
    j.request_payload, '$.comments[*]'
    COLUMNS (
        cid  VARCHAR(64)      PATH '$.comment_id',
        vt   TEXT             PATH '$.video_title',
        at   VARCHAR(255)     PATH '$.comment_author',
        uid  VARCHAR(128)     PATH '$.comment_author_uid',
        c    TEXT             PATH '$.comment_content',
        `like_count` INT      PATH '$.comment_like_count'
    )
) c
LEFT JOIN JSON_TABLE(
    j.result, '$.results[*]'
    COLUMNS (
        rcid VARCHAR(64)      PATH '$.comment_id',
        passed BOOLEAN        PATH '$.passed',
        is_car_owner BOOLEAN  PATH '$.is_car_owner',
        has_purchase_intent BOOLEAN PATH '$.has_purchase_intent',
        analysis TEXT         PATH '$.analysis'
    )
) r ON c.cid = r.rcid
WHERE j.job_type = 'comment_screening'
  AND j.status = 'success'
"""

# 统计 api_job 中 comment_screening 作业总数与累计评论数（每行一个作业）
SQL_COUNT = """
SELECT
    COUNT(*) AS job_count,
    COALESCE(SUM(JSON_LENGTH(request_payload, '$.comments')), 0) AS comment_count
FROM api_job
WHERE job_type = 'comment_screening'
  AND status = 'success'
"""


def parse_comments_from_payload(payload_json: str) -> list[dict]:
    """从 api_job.request_payload JSON 字符串解析评论数组。

    参数 payload_json 为 MySQL 返回的 JSON 列文本。
    返回评论字典列表；无 comments 字段或为空时返回 []。
    单个评论缺失 content 时以空串兜底。
    """
    if not payload_json:
        return []
    try:
        data = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return []

    comments = data.get("comments", []) if isinstance(data, dict) else []
    if not isinstance(comments, list):
        return []

    results = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        rec = dict(c)
        rec.setdefault("comment_content", "")
        results.append(rec)
    return results
